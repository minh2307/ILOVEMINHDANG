"""
Scheduler API Routes — FastAPI endpoints for the Project Scheduler.

Integrated into the existing dashboard FastAPI app (app/dashboard/server.py).
All endpoints are under /api/scheduler.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("scheduler.api")

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

# ---------------------------------------------------------------------------
# Lazy-load the scheduler engine to avoid import-time DB creation
# ---------------------------------------------------------------------------

def _get_scheduler():
    from app.config.settings import Settings
    from app.scheduler.engine import ProjectScheduler
    settings = Settings.from_env()
    return ProjectScheduler(db_path=settings.database_path)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SchedulerConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    start_time: Optional[str] = None          # HH:MM
    timezone: Optional[str] = None             # e.g. "Asia/Ho_Chi_Minh"


class SchedulerStatusResponse(BaseModel):
    phase: str
    scheduler_enabled: bool
    scheduled_start_time: str
    timezone: str
    current_time_local: str
    next_transition: Optional[str]
    last_transition_at: Optional[str]
    last_start_attempt: Optional[str]
    failed_services: list[str]
    services: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status", response_model=SchedulerStatusResponse)
def get_scheduler_status():
    """Return current scheduler phase and all service states."""
    try:
        scheduler = _get_scheduler()
        data = scheduler.get_status()
        return SchedulerStatusResponse(**data)
    except Exception as exc:
        logger.exception("[SCHEDULER API] Error fetching status")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/config")
def update_scheduler_config(req: SchedulerConfigRequest):
    """
    Update scheduler configuration (start time, timezone, enabled flag).
    Does not trigger an immediate phase transition.
    """
    try:
        scheduler = _get_scheduler()
        state = scheduler.update_config(
            start_time=req.start_time,
            tz=req.timezone,
            enabled=req.enabled,
        )
        return {"success": True, "phase": state.phase.value}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("[SCHEDULER API] Error updating config")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/tick")
def trigger_tick():
    """
    Manually trigger a scheduler evaluation tick.
    Useful after changing config or for testing.
    """
    try:
        scheduler = _get_scheduler()
        state = scheduler.tick()
        return {
            "success": True,
            "phase": state.phase.value,
            "failed_services": state.failed_services,
        }
    except Exception as exc:
        logger.exception("[SCHEDULER API] Error during tick")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/enable")
def enable_scheduler(req: SchedulerConfigRequest):
    """Enable the scheduler with an optional start time update."""
    try:
        scheduler = _get_scheduler()
        start_time = req.start_time or scheduler.state.scheduled_start_time
        tz = req.timezone or scheduler.state.timezone
        state = scheduler.enable(start_time=start_time, tz=tz)
        return {
            "success": True,
            "phase": state.phase.value,
            "scheduled_start_time": state.scheduled_start_time,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("[SCHEDULER API] Error enabling scheduler")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/disable")
def disable_scheduler():
    """Disable the scheduler (services keep running)."""
    try:
        scheduler = _get_scheduler()
        state = scheduler.disable()
        return {"success": True, "phase": state.phase.value}
    except Exception as exc:
        logger.exception("[SCHEDULER API] Error disabling scheduler")
        raise HTTPException(status_code=500, detail=str(exc))
