from __future__ import annotations

from datetime import UTC, datetime

from app.application.ports.job_repository_port import JobRepositoryPort
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.domain.enums.job_status import JobStatus
from app.domain.models.job_result import JobResult


class RetryJobUseCase:
    """Commit the failed stage's retry intent before scheduling it."""

    RETRY_STEP = {
        JobStatus.DOWNLOADREEL_FAILED: "download",
        JobStatus.GEMINI_FAILED: "ai",
        JobStatus.AI_FAILED: "ai",
        JobStatus.CDHA_FAILED: "cdha",
        JobStatus.FACEBOOK_PUBLISH_FAILED: "facebook_prepare",
        JobStatus.POST_URL_EXTRACTION_FAILED: "facebook_permalink",
        JobStatus.COMMENT_FAILED: "facebook_comment",
    }

    def __init__(
        self, repository: JobRepositoryPort, scheduler: ScheduleWorkflowJobsUseCase
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler

    async def execute(
        self,
        job_id: str,
        *,
        reason: str | None = None,
        requested_by: str = "operator",
    ) -> JobResult:
        job = self._repository.get_job(job_id)
        if job is None:
            return JobResult.failure_result(job_id, f"Job not found: {job_id}")
        if job.status is JobStatus.RETRY_PENDING:
            queued = await self._scheduler.schedule_job(job_id)
            return JobResult.success_result(
                job_id,
                {
                    "workflow_status": job.status.value,
                    "queued": queued,
                    "duplicate_retry": True,
                    "attempt_count": job.attempt_count,
                },
            )
        retry_step = self.RETRY_STEP.get(job.status)
        if retry_step is None:
            if job.status in {JobStatus.FACEBOOK_PUBLISH_UNCERTAIN, JobStatus.PUBLISH_RECONCILIATION_REQUIRED}:
                message = (
                    "Facebook publication is uncertain; reconcile it before any retry."
                )
            else:
                message = f"Job is not retryable from {job.status.value}"
            return JobResult.failure_result(job_id, message)
        if job.attempt_count >= job.max_attempts:
            return JobResult.failure_result(
                job_id,
                f"Maximum retry attempts reached ({job.attempt_count}/{job.max_attempts})",
            )
        requested_at = datetime.now(UTC).isoformat()
        next_attempt = job.attempt_count + 1
        retry_reason = (reason or job.error_message or "operator requested retry").strip()
        metadata = {
            "previous_failure_state": job.status.value,
            "failure_stage": retry_step,
            "retry_step": retry_step,
            "retry_reason": retry_reason,
            "retry_attempt": next_attempt,
            "max_attempts": job.max_attempts,
            "retry_requested_at": requested_at,
            "next_retry_at": requested_at,
            "retry_requested_by": requested_by,
        }
        job = self._repository.transition(
            job_id,
            JobStatus.RETRY_PENDING,
            details=metadata,
            data_patch=metadata,
            event_type="RETRY_REQUESTED",
            attempt=next_attempt,
        )
        queued = await self._scheduler.schedule_job(job_id)
        return JobResult.success_result(
            job_id,
            {
                "workflow_status": job.status.value,
                "queued": queued,
                "duplicate_retry": False,
                "attempt_count": job.attempt_count,
            },
        )
