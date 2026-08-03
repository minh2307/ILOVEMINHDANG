from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.errors import CDHAAnalysisTimeoutError


class CDHAState(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    CASE_PAGE_READY = "CASE_PAGE_READY"
    UPLOAD_READY = "UPLOAD_READY"
    UPLOAD_IN_PROGRESS = "UPLOAD_IN_PROGRESS"
    UPLOAD_COMPLETED = "UPLOAD_COMPLETED"
    ANALYSIS_QUEUED = "ANALYSIS_QUEUED"
    ANALYSIS_RUNNING = "ANALYSIS_RUNNING"
    ANALYSIS_COMPLETED = "ANALYSIS_COMPLETED"
    RESULT_READY = "RESULT_READY"
    CONTROL_NOT_FOUND = "CONTROL_NOT_FOUND"
    CONTROL_HIDDEN = "CONTROL_HIDDEN"
    CONTROL_DISABLED = "CONTROL_DISABLED"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PAGE_CLOSED = "PAGE_CLOSED"
    BROWSER_DISCONNECTED = "BROWSER_DISCONNECTED"
    UNKNOWN = "UNKNOWN"


class AuthenticationState(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    TWO_FACTOR_REQUIRED = "TWO_FACTOR_REQUIRED"
    CHECKPOINT_REQUIRED = "CHECKPOINT_REQUIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CDHAStateSnapshot:
    state: CDHAState
    current_url: str | None = None
    page_title: str | None = None
    selector_attempts: tuple[dict[str, Any], ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


class CDHAStateTimeoutError(CDHAAnalysisTimeoutError):
    def __init__(
        self,
        message: str,
        *,
        final_snapshot: CDHAStateSnapshot,
        timeout_seconds: float,
        phase: str = "CDHA_ANALYZING",
        operation: str = "wait_for_cdha_state",
        job_id: str | None = None,
    ) -> None:
        details = {
            "current_cdha_state": final_snapshot.state.value,
            "timeout_seconds": timeout_seconds,
            **final_snapshot.details,
        }
        super().__init__(
            message,
            phase=phase,
            operation=operation,
            job_id=job_id,
            details=details,
        )
        self.final_snapshot = final_snapshot
        self.timeout_seconds = timeout_seconds


async def wait_for_cdha_state(
    detect: Callable[[], Awaitable[CDHAStateSnapshot]],
    *,
    accepted_states: set[CDHAState] | frozenset[CDHAState],
    timeout_seconds: float,
    poll_interval_seconds: float,
    on_progress: Callable[[CDHAStateSnapshot], Any] | None = None,
    job_id: str | None = None,
    phase: str = "CDHA_ANALYZING",
    operation: str = "wait_for_cdha_state",
) -> CDHAStateSnapshot:
    """Poll semantic state with a monotonic deadline and change-only progress."""
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must be non-negative")
    started = time.monotonic()
    previous: CDHAState | None = None
    final = CDHAStateSnapshot(CDHAState.UNKNOWN)
    while True:
        final = await detect()
        if final.state is not previous:
            previous = final.state
            if on_progress is not None:
                emitted = on_progress(final)
                if inspect.isawaitable(emitted):
                    await emitted
        if final.state in accepted_states:
            return final
        elapsed = time.monotonic() - started
        if elapsed >= timeout_seconds:
            raise CDHAStateTimeoutError(
                f"CDHA state wait timed out in {phase}; final state={final.state.value}",
                final_snapshot=final,
                timeout_seconds=timeout_seconds,
                phase=phase,
                operation=operation,
                job_id=job_id,
            )
        await asyncio.sleep(min(poll_interval_seconds, timeout_seconds - elapsed))
