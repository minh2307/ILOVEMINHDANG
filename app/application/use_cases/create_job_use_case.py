from __future__ import annotations

from app.application.ports.job_repository_port import JobRepositoryPort
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.domain.models.job_result import JobResult
from app.services.reel_normalization import normalize_reel_url


class CreateJobUseCase:
    """Normalize, deduplicate, persist, and idempotently schedule a workflow job."""

    def __init__(
        self, repository: JobRepositoryPort, scheduler: ScheduleWorkflowJobsUseCase
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler

    async def execute(self, source_url: str, *, force: bool = False) -> JobResult:
        normalized = normalize_reel_url(source_url)
        existing = self._repository.find_latest_by_normalized_source_url(normalized)
        reused = existing is not None and not force
        job = existing if reused else self._repository.create_job(
            source_url,
            normalized_source_url=normalized,
            input_payload={"source_url": source_url},
        )
        queued = (
            await self._scheduler.schedule_job(job.job_id)
            if job.status in self._scheduler.ELIGIBLE
            else False
        )
        return JobResult.success_result(
            job.job_id,
            {
                "workflow_status": job.status.value,
                "reused": reused,
                "queued": queued,
            },
        )
