from __future__ import annotations

from app.application.ports.job_queue_port import JobQueuePort
from app.application.ports.job_repository_port import JobRepositoryPort
from app.domain.enums.job_status import JobStatus
from app.domain.enums.job_type import JobType
from app.domain.models.facebook_job import FacebookJob
from app.domain.policies.external_side_effect_policy import (
    large_upload_gate_required,
    large_upload_is_authorized,
    repository_facebook_submission_evidence,
)


class ScheduleWorkflowJobsUseCase:
    """Create idempotent queue work items for workflow states that can advance."""

    ELIGIBLE = frozenset(
        {
            JobStatus.CREATED,
            JobStatus.DOWNLOADREEL_RUNNING,
            JobStatus.DOWNLOADED,
            JobStatus.GEMINI_OPENING,
            JobStatus.GEMINI_GENERATING,
            JobStatus.AI_ANALYZING,
            JobStatus.CLINICAL_FACTORS_GENERATED,
            JobStatus.CDHA_OPENING,
            JobStatus.CDHA_UPLOADING,
            JobStatus.CDHA_ANALYZING,
            JobStatus.CDHA_ANALYZED,
            JobStatus.SCREENSHOTS_CAPTURING,
            JobStatus.SCREENSHOTS_CAPTURED,
            JobStatus.APPROVED,
            JobStatus.FACEBOOK_PREPARING,
            JobStatus.FACEBOOK_PUBLISHING,
            JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
            JobStatus.PUBLISH_RECONCILIATION_REQUIRED,
            JobStatus.FACEBOOK_PUBLISHED,
            JobStatus.POST_URL_EXTRACTING,
            JobStatus.POST_URL_EXTRACTED,
            JobStatus.COMMENT_ADDING,
            JobStatus.COMMENT_ADDED,
            JobStatus.RETRY_PENDING,
        }
    )

    def __init__(
        self,
        repository: JobRepositoryPort,
        queue: JobQueuePort,
        *,
        auto_approve_review: bool = False,
        require_facebook_confirmation: bool = True,
        max_facebook_reconciliation_attempts: int = 3,
        cdha_large_file_threshold_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self._repository = repository
        self._queue = queue
        eligible = set(self.ELIGIBLE)
        if auto_approve_review:
            eligible.add(JobStatus.WAITING_FOR_REVIEW)
        if not require_facebook_confirmation:
            eligible.add(JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW)
        self._eligible = frozenset(eligible)
        self._max_facebook_reconciliation_attempts = max(
            1, int(max_facebook_reconciliation_attempts)
        )
        self._cdha_large_file_threshold_bytes = max(
            1, int(cdha_large_file_threshold_bytes)
        )

    async def schedule_once(self, *, limit: int = 100) -> int:
        scheduled = 0
        for job in self._repository.list_jobs_by_status(set(self._eligible), limit=limit):
            scheduled += int(await self.schedule_job(job.job_id))
        return scheduled

    async def schedule_job(self, job_id: str) -> bool:
        job = self._repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        evidence = repository_facebook_submission_evidence(
            self._repository, job_id, job.data
        )
        if evidence.committed:
            enforce = getattr(
                self._repository, "enforce_facebook_submission_guard", None
            )
            if callable(enforce):
                job = enforce(job_id)
            if (
                job.status
                is JobStatus.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW
            ):
                return False
        if job.status not in self._eligible:
            raise ValueError(f"Job cannot advance from {job.status.value}")
        if (
            job.status is JobStatus.RETRY_PENDING
            and large_upload_gate_required(
                job, self._cdha_large_file_threshold_bytes
            )
            and not large_upload_is_authorized(
                job, self._cdha_large_file_threshold_bytes
            )
        ):
            return False
        work_item_id = f"{job.job_id}:{job.status.value}"
        if job.status is JobStatus.RETRY_PENDING:
            work_item_id = (
                f"{work_item_id}:attempt-{max(1, job.attempt_count)}"
            )
        max_attempts = job.max_attempts
        if job.status in {
            JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
            JobStatus.PUBLISH_RECONCILIATION_REQUIRED,
        }:
            max_attempts = max(
                max_attempts, self._max_facebook_reconciliation_attempts
            )
        elif job.status in {
            JobStatus.APPROVED,
            JobStatus.FACEBOOK_PREPARING,
            JobStatus.FACEBOOK_PUBLISHING,
        }:
            # Reserve one queue claim for the publish attempt itself; all
            # remaining claims are reconciliation-only.
            max_attempts = max(
                max_attempts, self._max_facebook_reconciliation_attempts + 1
            )
        return await self._queue.enqueue(
            FacebookJob(
                job_id=work_item_id,
                job_type=JobType.PROCESS_WORKFLOW,
                payload={
                    "workflow_job_id": job.job_id,
                    "scheduled_from_status": job.status.value,
                },
                max_attempts=max_attempts,
            )
        )

    async def schedule_publish_confirmation(self, job_id: str) -> bool:
        job = self._repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        if job.status is not JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW:
            raise ValueError(
                "Facebook publish confirmation requires "
                f"FACEBOOK_WAITING_FOR_MANUAL_REVIEW; got {job.status.value}"
            )
        return await self._queue.enqueue(
            FacebookJob(
                job_id=f"{job.job_id}:CONFIRMED_FACEBOOK_PUBLISH",
                job_type=JobType.PROCESS_WORKFLOW,
                payload={
                    "workflow_job_id": job.job_id,
                    "confirm_facebook_publish": True,
                },
                max_attempts=max(
                    job.max_attempts,
                    self._max_facebook_reconciliation_attempts + 1,
                ),
            )
        )
