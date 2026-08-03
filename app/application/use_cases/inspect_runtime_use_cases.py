from __future__ import annotations

from collections import Counter
from typing import Any

from app.domain.models.job_result import JobResult


class InspectBrowserUseCase:
    """Read browser/lock health without starting Chrome or acquiring the profile."""

    def __init__(self, browser_manager: Any, browser_lock: Any) -> None:
        self._browser_manager = browser_manager
        self._browser_lock = browser_lock

    async def execute(self) -> JobResult:
        health = await self._browser_manager.get_health()
        metadata_reader = getattr(self._browser_lock, "read_metadata", None)
        lock_metadata = (
            metadata_reader() if callable(metadata_reader) else None
        )
        lock_metadata = lock_metadata if isinstance(lock_metadata, dict) else {}
        safe_lock = {
            key: lock_metadata.get(key)
            for key in (
                "pid",
                "process_name",
                "hostname",
                "browser_profile",
                "browser_port",
                "job_id",
                "created_at",
                "heartbeat_at",
            )
            if lock_metadata.get(key) is not None
        }
        cdp_ready = await self._browser_manager.is_cdp_ready()
        return JobResult.success_result(
            "browser",
            {
                "browser_health_state": health.state.value,
                "browser_connected": health.browser_connected,
                "context_available": health.context_available,
                "cdp_ready": cdp_ready,
                "managed_pid": self._browser_manager.managed_pid(),
                "lock": safe_lock,
                "next_action": (
                    "Browser is available."
                    if cdp_ready
                    else "Run Quick/Full preflight or the official browser start command."
                ),
            },
        )


class InspectQueueUseCase:
    """Return operational lease metadata without exposing queue payloads."""

    def __init__(self, queue: Any) -> None:
        self._queue = queue

    async def execute(self) -> JobResult:
        records = await self._queue.list_records()
        safe_records = [
            {
                key: record.get(key)
                for key in (
                    "job_id",
                    "job_type",
                    "status",
                    "attempt_count",
                    "max_attempts",
                    "current_stage",
                    "claimed_by",
                    "lease_expires_at",
                    "last_heartbeat",
                    "next_retry_at",
                    "created_at",
                    "updated_at",
                    "completed_at",
                )
            }
            for record in records
        ]
        counts = Counter(str(record.get("status") or "UNKNOWN") for record in records)
        return JobResult.success_result(
            "queue",
            {
                "count": len(safe_records),
                "statuses": dict(sorted(counts.items())),
                "items": safe_records,
                "next_action": (
                    "Use status/retry/resume with a specific workflow job ID; "
                    "never edit SQLite manually."
                ),
            },
        )
