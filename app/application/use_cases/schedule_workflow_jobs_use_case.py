from __future__ import annotations

from app.application.ports.job_queue_port import JobQueuePort
from app.application.ports.job_repository_port import JobRepositoryPort
from app.domain.enums.job_status import JobStatus
from app.domain.enums.job_type import JobType
from app.domain.models.facebook_job import FacebookJob


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
            JobStatus.FACEBOOK_PUBLISHED,
            JobStatus.POST_URL_EXTRACTING,
            JobStatus.POST_URL_EXTRACTED,
            JobStatus.COMMENT_ADDING,
            JobStatus.COMMENT_ADDED,
            JobStatus.RETRY_PENDING,
        }
    )

    def __init__(self, repository: JobRepositoryPort, queue: JobQueuePort) -> None:
        self._repository = repository
        self._queue = queue

    async def schedule_once(self, *, limit: int = 100) -> int:
        scheduled = 0
        for job in self._repository.list_jobs_by_status(set(self.ELIGIBLE), limit=limit):
            scheduled += int(await self.schedule_job(job.job_id))
        return scheduled

    async def schedule_job(self, job_id: str) -> bool:
        job = self._repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        if job.status not in self.ELIGIBLE:
            raise ValueError(f"Job cannot advance from {job.status.value}")
        work_item_id = f"{job.job_id}:{job.status.value}"
        if job.status is JobStatus.RETRY_PENDING:
            work_item_id = (
                f"{work_item_id}:attempt-{max(1, job.attempt_count)}"
            )
        return await self._queue.enqueue(
            FacebookJob(
                job_id=work_item_id,
                job_type=JobType.PROCESS_WORKFLOW,
                payload={
                    "workflow_job_id": job.job_id,
                    "scheduled_from_status": job.status.value,
                },
                max_attempts=job.max_attempts,
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
                max_attempts=job.max_attempts,
            )
        )
