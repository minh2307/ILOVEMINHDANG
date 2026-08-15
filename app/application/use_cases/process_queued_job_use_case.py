from __future__ import annotations

from app.application.ports.job_repository_port import JobRepositoryPort
from app.application.use_cases.process_job_use_case import ProcessJobUseCase
from app.application.use_cases.retry_job_use_case import RetryJobUseCase
from app.domain.enums.job_status import JobStatus
from app.domain.models.facebook_job import FacebookJob
from app.domain.models.job_result import JobResult
from app.errors import FacebookReconciliationPendingError
from app.domain.policies.external_side_effect_policy import (
    repository_facebook_submission_evidence,
)


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
        if self._repository is not None:
            persisted = self._repository.get_job(workflow_job_id)
            if persisted is not None:
                evidence = repository_facebook_submission_evidence(
                    self._repository, workflow_job_id, persisted.data
                )
                scheduled_from = str(
                    work_item.payload.get("scheduled_from_status") or ""
                )
                if evidence.committed and scheduled_from in {
                    JobStatus.APPROVED.value,
                    JobStatus.FACEBOOK_PREPARING.value,
                    JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW.value,
                    JobStatus.RETRY_PENDING.value,
                }:
                    enforce = getattr(
                        self._repository,
                        "enforce_facebook_submission_guard",
                        None,
                    )
                    if callable(enforce):
                        enforce(workflow_job_id)
        result = await self._process_job.execute(
            workflow_job_id,
            allow_facebook_publish=bool(work_item.payload.get("confirm_facebook_publish")),
        )
        if result.success or self._repository is None or self._retry_job is None:
            return result
        persisted = self._repository.get_job(workflow_job_id)
        if persisted is not None:
            submitted = repository_facebook_submission_evidence(
                self._repository, workflow_job_id, persisted.data
            ).committed
            if submitted and persisted.status in {
                JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
                JobStatus.PUBLISH_RECONCILIATION_REQUIRED,
            }:
                raise FacebookReconciliationPendingError(
                    "Facebook reconciliation is pending; retry lookup without publishing",
                    job_id=workflow_job_id,
                    phase=persisted.status.value,
                    operation="reconcile_publication",
                    details={
                        "reconciliation_attempt": persisted.data.get(
                            "facebook_reconciliation_attempt", 0
                        ),
                        "publish_clicked": True,
                    },
                )
            if submitted and persisted.status is JobStatus.FACEBOOK_PUBLISH_FAILED:
                return result
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
