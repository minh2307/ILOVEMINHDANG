"""
Scheduler Persistence — SQLite-backed, reboot-safe.

Uses the existing database at data/jobs.sqlite3 (no new database).
Creates a 'scheduler_state' table on first use.

IMPORTANT: Only stores lightweight config/phase — does NOT duplicate
any job queue data or interfere with the existing jobs table.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.scheduler.state import SchedulerPhase, SchedulerState

logger = logging.getLogger("scheduler.persistence")


class SchedulerPersistence:
    """
    Reads/writes scheduler state to the existing SQLite database.

    Thread-safety: sqlite3 WAL mode + single-row upsert approach.
    The table holds exactly one row (scheduler_id = 'singleton').
    """

    SINGLETON_ID = "singleton"

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._ensure_table()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduler_state (
                    scheduler_id         TEXT PRIMARY KEY,
                    phase                TEXT NOT NULL DEFAULT 'STOPPED',
                    scheduled_start_time TEXT NOT NULL DEFAULT '08:00',
                    timezone             TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
                    scheduler_enabled    INTEGER NOT NULL DEFAULT 0,
                    last_transition_at   TEXT,
                    last_start_attempt   TEXT,
                    failed_services      TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self) -> SchedulerState:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduler_state WHERE scheduler_id = ?",
                (self.SINGLETON_ID,),
            ).fetchone()

        if row is None:
            logger.info("[SCHEDULER] No persisted state found — starting fresh.")
            return SchedulerState()

        failed_raw = row["failed_services"] or ""
        failed_list = [s.strip() for s in failed_raw.split(",") if s.strip()]

        return SchedulerState(
            phase=SchedulerPhase(row["phase"]),
            scheduled_start_time=row["scheduled_start_time"],
            timezone=row["timezone"],
            scheduler_enabled=bool(row["scheduler_enabled"]),
            last_transition_at=row["last_transition_at"],
            last_start_attempt=row["last_start_attempt"],
            failed_services=failed_list,
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, state: SchedulerState) -> None:
        failed_csv = ",".join(state.failed_services)
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO scheduler_state (
                    scheduler_id, phase, scheduled_start_time, timezone,
                    scheduler_enabled, last_transition_at, last_start_attempt,
                    failed_services
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scheduler_id) DO UPDATE SET
                    phase                = excluded.phase,
                    scheduled_start_time = excluded.scheduled_start_time,
                    timezone             = excluded.timezone,
                    scheduler_enabled    = excluded.scheduler_enabled,
                    last_transition_at   = excluded.last_transition_at,
                    last_start_attempt   = excluded.last_start_attempt,
                    failed_services      = excluded.failed_services
            """, (
                self.SINGLETON_ID,
                state.phase.value,
                state.scheduled_start_time,
                state.timezone,
                int(state.scheduler_enabled),
                state.last_transition_at or now,
                state.last_start_attempt,
                failed_csv,
            ))
            conn.commit()

        logger.debug("[SCHEDULER] State persisted: phase=%s", state.phase.value)

    # ------------------------------------------------------------------
    # Convenience: update single fields
    # ------------------------------------------------------------------

    def update_config(
        self,
        *,
        scheduled_start_time: Optional[str] = None,
        timezone: Optional[str] = None,
        scheduler_enabled: Optional[bool] = None,
    ) -> SchedulerState:
        """Load, patch config fields, persist, and return updated state."""
        state = self.load()
        if scheduled_start_time is not None:
            state.scheduled_start_time = scheduled_start_time
        if timezone is not None:
            state.timezone = timezone
        if scheduler_enabled is not None:
            state.scheduler_enabled = scheduler_enabled
        self.save(state)
        return state
