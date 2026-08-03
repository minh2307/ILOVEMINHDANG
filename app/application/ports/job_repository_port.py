from __future__ import annotations

from typing import Any, Protocol

from app.domain.enums.job_status import JobStatus
from app.domain.enums.job_type import JobType
from app.domain.models.job import Job
from app.domain.models.job_event import JobEvent


class JobRepositoryPort(Protocol):
    """Persistence contract for the authoritative workflow job aggregate."""

    def initialize(self) -> None: ...

    def create_job(
        self,
        source_url: str,
        *,
        job_id: str | None = None,
        normalized_source_url: str | None = None,
        data: dict[str, Any] | None = None,
        job_type: JobType = JobType.PROCESS_WORKFLOW,
        input_payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> Job: ...

    def get_job(self, job_id: str) -> Job | None: ...

    def find_latest_by_normalized_source_url(
        self, normalized_source_url: str
    ) -> Job | None: ...

    def list_jobs(self, limit: int = 100) -> list[Job]: ...

    def list_jobs_by_status(
        self, statuses: set[JobStatus], *, limit: int = 100
    ) -> list[Job]: ...

    def transition(
        self,
        job_id: str,
        target: JobStatus,
        *,
        details: dict[str, Any] | None = None,
        data_patch: dict[str, Any] | None = None,
        event_type: str = "JOB_STATE_CHANGED",
        attempt: int = 0,
    ) -> Job: ...

    def update_data(self, job_id: str, data_patch: dict[str, Any]) -> Job: ...

    def record_event(
        self,
        job_id: str,
        *,
        details: dict[str, Any],
        event_type: str = "JOB_STATE_CHANGED",
        attempt: int = 0,
    ) -> JobEvent: ...

    def list_events(self, job_id: str) -> list[JobEvent]: ...


class LegacyDispatchRepositoryPort(Protocol):
    """Compatibility seam for inactive, pre-convergence single-action use cases."""

    def mark_running(self, job_id: str) -> None: ...

    def mark_success(self, job_id: str, data: dict[str, Any] | None = None) -> None: ...

    def mark_failed(self, job_id: str, error: str) -> None: ...
