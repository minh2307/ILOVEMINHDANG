from __future__ import annotations

from app.application.ports.job_repository_port import JobRepositoryPort
from app.domain.enums.job_status import JobStatus
from app.domain.models.job_result import JobResult


class CancelJobUseCase:
    """Cancel through the authoritative state machine while preserving artifacts."""

    def __init__(self, repository: JobRepositoryPort) -> None:
        self._repository = repository

    async def execute(self, job_id: str, *, reason: str = "operator request") -> JobResult:
        job = self._repository.get_job(job_id)
        if job is None:
            return JobResult.failure_result(job_id, f"Job not found: {job_id}")
        if job.status is JobStatus.CANCELLED:
            return JobResult.success_result(
                job_id, {"workflow_status": job.status.value, "already_cancelled": True}
            )
        try:
            updated = self._repository.transition(
                job_id,
                JobStatus.CANCELLED,
                details={"reason": reason, "cancelled_by": "operator"},
            )
        except ValueError as exc:
            return JobResult.failure_result(job_id, str(exc))
        return JobResult.success_result(
            job_id,
            {"workflow_status": updated.status.value, "already_cancelled": False},
        )
