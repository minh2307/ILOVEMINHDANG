"""
Project Scheduler — Core State Machine Engine

Responsibilities:
  - Determine current phase based on time vs scheduled_start_time.
  - Transition UI_ONLY → STARTING_FULL → FULL_RUNNING at the right moment.
  - Detect and recover correct phase after machine reboot.
  - Start only the services appropriate for each phase.
  - Never create duplicate processes.
  - Never trigger job execution, Facebook actions, or CDHA actions.
  - Persist all phase transitions to SQLite for restart-safety.

NOT responsible for:
  - Job queue dispatch
  - Facebook/CDHA automation
  - Browser management
  - Any external side effects
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from app.scheduler.state import SchedulerPhase, SchedulerState, ServiceState, ServiceStatus
from app.scheduler.persistence import SchedulerPersistence
from app.scheduler import service_manager

logger = logging.getLogger("scheduler.engine")

# ---------------------------------------------------------------------------
# Phase → required services map
# ---------------------------------------------------------------------------
# Phase 1: Only dashboard (frontend). Nothing with side effects.
UI_ONLY_SERVICES: list[str] = ["dashboard"]

# Phase 2: Full system. Order matters — Ollama before worker.
FULL_RUNNING_SERVICES: list[str] = ["dashboard", "ollama", "orchestrator", "worker"]

# Services that are required for FULL_RUNNING health check
REQUIRED_FOR_FULL: set[str] = {"dashboard", "worker"}


class ProjectScheduler:
    """
    Core scheduler engine.

    Usage:
        scheduler = ProjectScheduler(db_path=Path("data/jobs.sqlite3"))
        scheduler.tick()   # call periodically (e.g., every 30 s)
    """

    def __init__(self, db_path: Path) -> None:
        self._persistence = SchedulerPersistence(db_path)
        self._state: Optional[SchedulerState] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> SchedulerState:
        if self._state is None:
            self._state = self._persistence.load()
        return self._state

    def reload(self) -> SchedulerState:
        """Force reload from DB (e.g., after API update)."""
        self._state = self._persistence.load()
        return self._state

    def tick(self) -> SchedulerState:
        """
        Main loop tick: evaluate current time vs schedule and drive transitions.

        Safe to call repeatedly; idempotent within a phase.
        Returns the current state after any transitions.
        """
        state = self.reload()

        if not state.scheduler_enabled:
            logger.debug("[SCHEDULER] Scheduler disabled — no action.")
            return state

        # Refresh service status snapshots
        state.services = service_manager.check_all_services()
        current_phase = state.phase

        logger.info(
            "[SCHEDULER] Current time: %s | Scheduled start: %s | State: %s",
            self._now_local_str(state.timezone),
            state.scheduled_start_time,
            current_phase.value,
        )
        for svc_name, svc_state in state.services.items():
            logger.info("[SCHEDULER] %-14s %s", svc_name + ":", svc_state.status.value)

        # --- Decide target phase based on time ---
        target_phase = self._compute_target_phase(state)

        # --- Drive state machine ---
        if current_phase == SchedulerPhase.STOPPED:
            if target_phase in (SchedulerPhase.UI_ONLY, SchedulerPhase.FULL_RUNNING):
                state = self._enter_ui_only(state)
            if target_phase == SchedulerPhase.FULL_RUNNING:
                state = self._enter_full_running(state)

        elif current_phase == SchedulerPhase.UI_ONLY:
            if target_phase == SchedulerPhase.FULL_RUNNING:
                state = self._enter_full_running(state)

        elif current_phase in (SchedulerPhase.STARTING_FULL, SchedulerPhase.DEGRADED):
            if target_phase == SchedulerPhase.FULL_RUNNING:
                # Retry any failed services
                state = self._enter_full_running(state)
            elif target_phase == SchedulerPhase.UI_ONLY:
                state = self._downgrade_to_ui_only(state)

        elif current_phase == SchedulerPhase.FULL_RUNNING:
            if target_phase == SchedulerPhase.UI_ONLY:
                state = self._downgrade_to_ui_only(state)
            else:
                # Stay; just verify services are healthy
                self._verify_full_running(state)

        # Persist updated state (without service snapshots — those are ephemeral)
        self._save(state)
        return state

    def enable(self, *, start_time: str, tz: str = "Asia/Ho_Chi_Minh") -> SchedulerState:
        """Enable the scheduler with a new start time. Triggers a tick."""
        _validate_time(start_time)
        state = self._persistence.update_config(
            scheduled_start_time=start_time,
            timezone=tz,
            scheduler_enabled=True,
        )
        self._state = state
        logger.info("[SCHEDULER] Enabled. Start time=%s TZ=%s", start_time, tz)
        return self.tick()

    def disable(self) -> SchedulerState:
        """Disable the scheduler (stops auto-transitions, services keep running)."""
        state = self._persistence.update_config(scheduler_enabled=False)
        self._state = state
        logger.info("[SCHEDULER] Disabled.")
        return state

    def get_status(self) -> dict:
        """Return full status dict including live service snapshots."""
        state = self.reload()
        state.services = service_manager.check_all_services()
        data = state.to_dict()
        data["next_transition"] = self._next_transition_str(state)
        data["current_time_local"] = self._now_local_str(state.timezone)
        return data

    def update_config(
        self,
        *,
        start_time: Optional[str] = None,
        tz: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> SchedulerState:
        """Update config fields without triggering a transition."""
        if start_time is not None:
            _validate_time(start_time)
        state = self._persistence.update_config(
            scheduled_start_time=start_time,
            timezone=tz,
            scheduler_enabled=enabled,
        )
        self._state = state
        return state

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def _enter_ui_only(self, state: SchedulerState) -> SchedulerState:
        logger.info("[SCHEDULER] Transition %s → UI_ONLY", state.phase.value)
        state = self._transition(state, SchedulerPhase.UI_ONLY)

        for svc in UI_ONLY_SERVICES:
            result = service_manager.start_service(svc)
            state.services[svc] = result
            if result.status == ServiceStatus.FAILED:
                logger.error("[SCHEDULER] Failed to start UI service: %s", svc)

        return state

    def _enter_full_running(self, state: SchedulerState) -> SchedulerState:
        logger.info("[SCHEDULER] Transition %s → STARTING_FULL", state.phase.value)
        state = self._transition(state, SchedulerPhase.STARTING_FULL)
        state.last_start_attempt = datetime.now(timezone.utc).isoformat()
        failed: list[str] = []

        for svc in FULL_RUNNING_SERVICES:
            result = service_manager.start_service(svc)
            state.services[svc] = result

            if result.status == ServiceStatus.FAILED:
                failed.append(svc)
                logger.error("[SCHEDULER] %s failed to start.", svc)
            else:
                logger.info("[SCHEDULER] %s healthy.", svc)

        state.failed_services = failed

        if any(svc in failed for svc in REQUIRED_FOR_FULL):
            logger.error(
                "[SCHEDULER] Critical service(s) failed: %s → DEGRADED",
                ", ".join(f for f in failed if f in REQUIRED_FOR_FULL),
            )
            state = self._transition(state, SchedulerPhase.DEGRADED)
        else:
            logger.info("[SCHEDULER] Transition STARTING_FULL → FULL_RUNNING")
            state = self._transition(state, SchedulerPhase.FULL_RUNNING)

        return state

    def _downgrade_to_ui_only(self, state: SchedulerState) -> SchedulerState:
        logger.info("[SCHEDULER] Transition %s → UI_ONLY (Downgrading)", state.phase.value)
        state = self._transition(state, SchedulerPhase.UI_ONLY)

        # Stop services that are in FULL_RUNNING but not in UI_ONLY
        for svc in FULL_RUNNING_SERVICES:
            if svc not in UI_ONLY_SERVICES:
                result = service_manager.stop_service(svc)
                state.services[svc] = result

        return state

    def _verify_full_running(self, state: SchedulerState) -> None:
        """Periodic health verification when already FULL_RUNNING."""
        degraded = False
        failed_services = []
        for svc in REQUIRED_FOR_FULL:
            svc_state = state.services.get(svc)
            if svc_state and svc_state.status != ServiceStatus.RUNNING:
                logger.warning("[SCHEDULER] %s went offline while FULL_RUNNING!", svc)
                failed_services.append(svc)
                degraded = True
                
        if degraded:
            logger.error("[SCHEDULER] Transition FULL_RUNNING → DEGRADED due to offline services: %s", ", ".join(failed_services))
            state.failed_services = failed_services
            state = self._transition(state, SchedulerPhase.DEGRADED)

    # ------------------------------------------------------------------
    # Time logic
    # ------------------------------------------------------------------

    def _compute_target_phase(self, state: SchedulerState) -> SchedulerPhase:
        """
        Determine the target phase based on current time vs scheduled_start_time.

        Rules:
        - current_time < scheduled_start_time → UI_ONLY
        - current_time >= scheduled_start_time → FULL_RUNNING
        """
        now = _now_in_tz(state.timezone)
        start_today = _parse_time_today(state.scheduled_start_time, state.timezone)

        if now >= start_today:
            return SchedulerPhase.FULL_RUNNING
        return SchedulerPhase.UI_ONLY

    def _next_transition_str(self, state: SchedulerState) -> Optional[str]:
        """Human-readable description of next scheduled transition."""
        if state.phase in (SchedulerPhase.FULL_RUNNING, SchedulerPhase.DEGRADED):
            return None  # No more transitions today
        if not state.scheduler_enabled:
            return None
        start_today = _parse_time_today(state.scheduled_start_time, state.timezone)
        return start_today.isoformat()

    def _now_local_str(self, tz: str) -> str:
        return _now_in_tz(tz).strftime("%H:%M:%S %Z")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _transition(
        self, state: SchedulerState, new_phase: SchedulerPhase
    ) -> SchedulerState:
        state.phase = new_phase
        state.last_transition_at = datetime.now(timezone.utc).isoformat()
        return state

    def _save(self, state: SchedulerState) -> None:
        self._persistence.save(state)


# ---------------------------------------------------------------------------
# Time utilities (always timezone-aware)
# ---------------------------------------------------------------------------

def _now_in_tz(tz_name: str) -> datetime:
    """Return the current datetime in the given timezone."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        # Fallback for Python < 3.9
        try:
            import pytz
            tz = pytz.timezone(tz_name)
        except Exception:
            logger.warning("[SCHEDULER] Unknown timezone '%s' — using UTC.", tz_name)
            return datetime.now(timezone.utc)
    return datetime.now(tz)


def _parse_time_today(hhmm: str, tz_name: str) -> datetime:
    """
    Return today's date at HH:MM in the given timezone, timezone-aware.
    """
    now = _now_in_tz(tz_name)
    hour, minute = map(int, hhmm.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _validate_time(hhmm: str) -> None:
    parts = hhmm.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format '{hhmm}' — expected HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Time out of range: {hhmm}")
