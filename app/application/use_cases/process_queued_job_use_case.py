from __future__ import annotations

from app.application.ports.job_repository_port import JobRepositoryPort
from app.application.use_cases.process_job_use_case import ProcessJobUseCase
from app.application.use_cases.retry_job_use_case import RetryJobUseCase
from app.domain.enums.job_status import JobStatus
from app.domain.models.facebook_job import FacebookJob
from app.domain.models.job_result import JobResult


class ProcessQueuedJobUseCase:
    """Translate a durable queue work item into a workflow orchestration run."""

    def __init__(
        self,
        process_job: ProcessJobUseCase,
        repository: JobRepositoryPort | None = None,
        retry_job: RetryJobUseCase | None = None,
    ) -> None:
        self._process_job = process_job
        self._repository = repository
        self._retry_job = retry_job

    async def execute(self, work_item: FacebookJob) -> JobResult:
        workflow_job_id = str(work_item.payload.get("workflow_job_id", "")).strip()
        if not workflow_job_id:
            return JobResult.failure_result(
                work_item.job_id, "PROCESS_WORKFLOW payload requires workflow_job_id"
            )
        result = await self._process_job.execute(
            workflow_job_id,
            allow_facebook_publish=bool(work_item.payload.get("confirm_facebook_publish")),
        )
        if result.success or self._repository is None or self._retry_job is None:
            return result
        persisted = self._repository.get_job(workflow_job_id)
        if persisted is None or persisted.status is not JobStatus.FACEBOOK_PUBLISH_FAILED:
            return result
        retry = await self._retry_job.execute(
            workflow_job_id,
            reason=result.error or "definite Facebook publication failure",
            requested_by="worker",
        )
        if not retry.success:
            return result
        return JobResult.success_result(
            workflow_job_id,
            {
                "workflow_status": JobStatus.RETRY_PENDING.value,
                "retry_scheduled": True,
                "queued": retry.data.get("queued", False),
                "attempt_count": retry.data.get("attempt_count"),
                "previous_error": result.error,
            },
        )
