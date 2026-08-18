"""
Scheduler State Machine Types

State transitions:
    STOPPED
       ↓ (scheduler enabled + UI services started)
    UI_ONLY
       ↓ (current_time >= scheduled_start_time)
    STARTING_FULL
       ↓ (all health checks pass)
    FULL_RUNNING

On any service startup failure:
    STARTING_FULL → DEGRADED

DEGRADED does NOT auto-recover to STOPPED while frontend is still active.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class SchedulerPhase(str, enum.Enum):
    STOPPED = "STOPPED"
    UI_ONLY = "UI_ONLY"
    STARTING_FULL = "STARTING_FULL"
    FULL_RUNNING = "FULL_RUNNING"
    DEGRADED = "DEGRADED"


class ServiceStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass
class ServiceState:
    name: str
    status: ServiceStatus = ServiceStatus.UNKNOWN
    pid: Optional[int] = None
    error: Optional[str] = None
    last_checked_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "pid": self.pid,
            "error": self.error,
            "last_checked_at": self.last_checked_at,
        }


@dataclass
class SchedulerState:
    """
    Authoritative, persisted scheduler state.

    Fields that must survive process restart (written to SQLite):
    - phase
    - scheduled_start_time  (HH:MM)
    - timezone
    - scheduler_enabled
    - last_transition_at    (ISO-8601)
    - last_start_attempt    (ISO-8601)
    - failed_services       (comma-separated service names)
    """
    phase: SchedulerPhase = SchedulerPhase.STOPPED
    scheduled_start_time: str = "08:00"          # HH:MM in project timezone
    timezone: str = "Asia/Ho_Chi_Minh"
    scheduler_enabled: bool = False
    last_transition_at: Optional[str] = None
    last_start_attempt: Optional[str] = None
    failed_services: list[str] = field(default_factory=list)

    # Runtime-only (not persisted): per-service status snapshots
    services: dict[str, ServiceState] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "scheduled_start_time": self.scheduled_start_time,
            "timezone": self.timezone,
            "scheduler_enabled": self.scheduler_enabled,
            "last_transition_at": self.last_transition_at,
            "last_start_attempt": self.last_start_attempt,
            "failed_services": self.failed_services,
            "services": {k: v.to_dict() for k, v in self.services.items()},
        }
