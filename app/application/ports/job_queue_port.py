from __future__ import annotations

from typing import Any, Protocol

from app.domain.enums.job_status import JobStatus
from app.domain.models.facebook_job import FacebookJob


class JobQueuePort(Protocol):
    async def enqueue(self, job: FacebookJob) -> bool:
        ...

    async def dequeue(
        self, *, worker_id: str = "legacy-worker", lease_seconds: float = 120.0
    ) -> FacebookJob | None:
        ...

    async def heartbeat(
        self, job_id: str, *, worker_id: str, lease_seconds: float = 120.0
    ) -> bool:
        ...

    async def set_state(
        self,
        job_id: str,
        state: JobStatus,
        *,
        event_type: str = "JOB_STATE_CHANGED",
        details: dict[str, Any] | None = None,
    ) -> bool:
        ...

    async def record_event(
        self, job_id: str, event_type: str, *, details: dict[str, Any] | None = None
    ) -> None:
        ...

    async def retry(self, job_id: str, error: str, delay_seconds: float) -> bool:
        ...

    async def recover_jobs(self) -> int:
        ...

    async def complete(self, job_id: str) -> None:
        ...

    async def fail(self, job_id: str, error: str) -> None:
        ...

    async def get_record(self, job_id: str) -> dict[str, Any] | None:
        ...

    async def list_records(self) -> list[dict[str, Any]]:
        ...

    async def list_events(self, job_id: str) -> list[dict[str, Any]]:
        ...
