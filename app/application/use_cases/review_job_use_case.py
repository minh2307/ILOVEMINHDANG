from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.application.ports.job_repository_port import JobRepositoryPort
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.domain.enums.job_status import JobStatus
from app.domain.models.job_result import JobResult


class ReviewJobUseCase:
    """Run the review adapter and schedule only the resulting persisted boundary."""

    def __init__(
        self,
        repository: JobRepositoryPort,
        scheduler: ScheduleWorkflowJobsUseCase,
        reviewer: Callable[[str], Any],
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler
        self._reviewer = reviewer

    async def execute(self, job_id: str) -> JobResult:
        job = self._repository.get_job(job_id)
        if job is None:
            return JobResult.failure_result(job_id, f"Job not found: {job_id}")
        if job.status is not JobStatus.WAITING_FOR_REVIEW:
            return JobResult.failure_result(
                job_id,
                f"Review requires WAITING_FOR_REVIEW; got {job.status.value}",
            )
        decision = self._reviewer(job_id)
        updated = self._repository.get_job(job_id)
        if updated is None:
            return JobResult.failure_result(job_id, "Job disappeared during review")
        queued = False
        if updated.status in self._scheduler.ELIGIBLE:
            queued = await self._scheduler.schedule_job(job_id)
        return JobResult.success_result(
            job_id,
            {
                "workflow_status": updated.status.value,
                "decision": str(getattr(decision, "action", decision)),
                "exit_code": int(getattr(decision, "exit_code", 0)),
                "queued": queued,
            },
        )
