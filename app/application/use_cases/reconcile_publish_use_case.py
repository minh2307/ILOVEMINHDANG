from __future__ import annotations

import logging

from app.adapters.facebook_adapter import FacebookPublisherAdapter
from app.domain.enums.job_status import JobStatus as WorkflowStatus
from app.models.results import FacebookPublishResult
from app.repositories.job_repository import JobRepository


class ReconcilePublishUseCase:
    """Explicitly verify and reconcile an uncertain Facebook publication attempt.

    Safety contract: This use case NEVER transitions through FACEBOOK_PUBLISHING
    and NEVER clicks the Publish button. It only queries the feed to check if the
    post already exists and transitions to FACEBOOK_PUBLISHED or keeps the job in
    FACEBOOK_PUBLISH_UNCERTAIN.
    """

    _ALLOWED_STATUSES = {
        WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED,
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
    }

    def __init__(
        self,
        repository: JobRepository,
        facebook_publisher: FacebookPublisherAdapter,
        logger: logging.Logger | None = None,
    ) -> None:
        self.repository = repository
        self.publisher = facebook_publisher
        self.logger = logger or logging.getLogger("cdha_pipeline.reconcile")

    async def execute(self, job_id: str) -> FacebookPublishResult:
        job = self.repository.get_job(job_id)
        if not job:
            raise LookupError(f"Job not found: {job_id}")

        if job.status not in self._ALLOWED_STATUSES:
            raise ValueError(
                f"Job must be in {[s.value for s in self._ALLOWED_STATUSES]} to be reconciled. "
                f"Current status: {job.status.value}"
            )

        if job.status is WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN:
            self.repository.transition(
                job_id,
                WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED,
                event_type="FACEBOOK_PUBLICATION_RECONCILIATION_STARTED",
                details={"publish_clicked": False},
            )

        try:
            result = await self.publisher.reconcile_publication(job_id=job_id)
            return result
        except Exception as exc:
            self.logger.error("Reconciliation failed for job %s: %s", job_id, exc)
            raise
