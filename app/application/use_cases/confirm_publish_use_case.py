from __future__ import annotations

from app.application.ports.job_repository_port import JobRepositoryPort
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.domain.enums.job_status import JobStatus
from app.domain.models.job_result import JobResult


class ConfirmPublishUseCase:
    """Validate explicit operator intent before queueing the publish boundary."""

    def __init__(
        self, repository: JobRepositoryPort, scheduler: ScheduleWorkflowJobsUseCase
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler

    @staticmethod
    def expected_phrase(job_id: str) -> str:
        return f"PUBLISH {job_id}"

    async def execute(self, job_id: str, *, confirmation: str) -> JobResult:
        job = self._repository.get_job(job_id)
        if job is None:
            return JobResult.failure_result(job_id, f"Job not found: {job_id}")
        if job.status is not JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW:
            return JobResult.failure_result(
                job_id,
                "Facebook publish confirmation requires "
                f"FACEBOOK_WAITING_FOR_MANUAL_REVIEW; got {job.status.value}",
            )
        if confirmation.strip() != self.expected_phrase(job_id):
            return JobResult.failure_result(
                job_id, "Publication confirmation phrase did not match."
            )
        queued = await self._scheduler.schedule_publish_confirmation(job_id)
        return JobResult.success_result(
            job_id,
            {
                "workflow_status": job.status.value,
                "queued": queued,
                "duplicate_confirmation": not queued,
            },
        )
