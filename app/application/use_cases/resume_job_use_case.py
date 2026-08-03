from __future__ import annotations

from app.application.ports.job_repository_port import JobRepositoryPort
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.domain.enums.job_status import JobStatus
from app.domain.models.job_result import JobResult


class ResumeJobUseCase:
    """Resume from a persisted boundary without repeating verified side effects."""

    _FAILURES = frozenset(
        {
            JobStatus.DOWNLOADREEL_FAILED,
            JobStatus.GEMINI_FAILED,
            JobStatus.AI_FAILED,
            JobStatus.CDHA_FAILED,
            JobStatus.FACEBOOK_PUBLISH_FAILED,
            JobStatus.POST_URL_EXTRACTION_FAILED,
            JobStatus.COMMENT_FAILED,
            JobStatus.FAILED,
        }
    )

    def __init__(
        self, repository: JobRepositoryPort, scheduler: ScheduleWorkflowJobsUseCase
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler

    async def execute(self, job_id: str) -> JobResult:
        job = self._repository.get_job(job_id)
        if job is None:
            return JobResult.failure_result(job_id, f"Job not found: {job_id}")
        if job.status is JobStatus.COMPLETED:
            return JobResult.success_result(
                job_id, {"workflow_status": job.status.value, "queued": False}
            )
        if job.status is JobStatus.FACEBOOK_PUBLISH_UNCERTAIN:
            return JobResult.failure_result(
                job_id,
                "Facebook publication is uncertain and requires reconciliation; "
                "automatic retry is blocked.",
            )
        if job.status in self._FAILURES:
            return JobResult.failure_result(
                job_id,
                f"Job is in {job.status.value}; use the official retry command.",
            )
        manual_commands = {
            JobStatus.WAITING_FOR_REVIEW: "review",
            JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW: "confirm-publish",
            JobStatus.WAITING_FOR_AUTH_REVIEW: "complete authentication, then resume",
            JobStatus.REJECTED: "create-job",
            JobStatus.BLOCKED: "resolve the blocking event before retry",
            JobStatus.CANCELLED: "create-job",
        }
        if job.status in manual_commands:
            return JobResult.failure_result(
                job_id,
                f"Job requires manual action from {job.status.value}: "
                f"{manual_commands[job.status]}",
            )
        try:
            queued = await self._scheduler.schedule_job(job_id)
        except ValueError as exc:
            return JobResult.failure_result(job_id, str(exc))
        return JobResult.success_result(
            job_id,
            {"workflow_status": job.status.value, "queued": queued},
        )
