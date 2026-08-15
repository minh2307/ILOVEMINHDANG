from __future__ import annotations

import logging

from app.adapters.facebook_adapter import FacebookPublisherAdapter
from app.browser.facebook_client import FacebookWebClient
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
        WorkflowStatus.FACEBOOK_PUBLISHING,
        WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED,
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
        WorkflowStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED,
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

        enforce = getattr(self.repository, "enforce_facebook_submission_guard", None)
        if callable(enforce):
            job = enforce(job_id)
        if (
            job.status
            is WorkflowStatus.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW
        ):
            return FacebookPublishResult(
                False,
                "POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW",
                target_url=str(job.data.get("facebook_target_url") or ""),
                job_id=job_id,
                error="Multiple durable Facebook submit attempts require manual review",
            )

        if job.status not in self._ALLOWED_STATUSES:
            raise ValueError(
                f"Job must be in {[s.value for s in self._ALLOWED_STATUSES]} to be reconciled. "
                f"Current status: {job.status.value}"
            )

        persisted_permalink = str(job.data.get("facebook_post_url") or "").strip()
        if persisted_permalink:
            try:
                canonical = FacebookWebClient.normalize_permalink(
                    persisted_permalink, base_url=persisted_permalink
                )
                post_id = FacebookWebClient.extract_post_id(canonical)
            except ValueError:
                canonical = ""
                post_id = None
            if canonical and post_id and (
                "/posts/" in canonical
                or "story_fbid=" in canonical
                or "photo.php" in canonical
                or str(post_id).startswith("pfbid")
            ):
                if job.status is not WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED:
                    self.repository.transition(
                        job_id,
                        WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED,
                        event_type="RECONCILIATION_STATE_ENTERED",
                        details={"publish_clicked": False, "permalink_short_circuit": True},
                    )
                self.repository.transition(
                    job_id,
                    WorkflowStatus.FACEBOOK_PUBLISHED,
                    event_type="FACEBOOK_PUBLICATION_RECONCILED_FROM_PERMALINK",
                    details={"publish_clicked": False, "timeline_scanned": False},
                    data_patch={
                        "facebook_post_url": canonical,
                        "facebook_post_id": post_id,
                        "facebook_publication_verified": True,
                        "facebook_publication_state": "PUBLISHED_CONFIRMED",
                        "facebook_submission_status": "RECONCILED_VERIFIED",
                        "facebook_verification_method": "persisted_permalink",
                    },
                )
                return FacebookPublishResult(
                    True,
                    "PUBLISHED_VERIFIED",
                    target_url=str(job.data.get("facebook_target_url") or ""),
                    post_id=post_id,
                    permalink=canonical,
                    post_url=canonical,
                    verification_method="persisted_permalink",
                    job_id=job_id,
                )

        if job.status is not WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED:
            self.repository.transition(
                job_id,
                WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED,
                event_type="RECONCILIATION_STATE_ENTERED",
                details={
                    "publish_clicked": False,
                    "recovery_from_status": job.status.value,
                },
            )

        begin = getattr(self.repository, "begin_facebook_reconciliation", None)
        if callable(begin):
            begin(job_id)

        try:
            result = await self.publisher.reconcile_publication(job_id=job_id)
            current = self.repository.get_job(job_id)
            if result.status == "POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW":
                matching = list(result.diagnostics.get("matching_permalinks") or [])
                quarantine = getattr(
                    self.repository, "quarantine_possible_duplicate", None
                )
                if callable(quarantine):
                    quarantine(
                        job_id,
                        expected_fingerprint=str(
                            job.data.get("facebook_content_hash") or ""
                        ),
                        reason="multiple reconciliation matches",
                        matching_permalinks=matching,
                    )
                return result
            if result.success and (
                result.permalink or result.post_url
            ) and current is not None and current.status in self._ALLOWED_STATUSES:
                permalink = result.permalink or result.post_url
                self.repository.transition(
                    job_id,
                    WorkflowStatus.FACEBOOK_PUBLISHED,
                    event_type="FACEBOOK_PUBLICATION_RECONCILED",
                    details={"publish_clicked": False},
                    data_patch={
                        "facebook_post_url": permalink,
                        "facebook_post_id": result.post_id,
                        "facebook_publication_verified": True,
                        "facebook_publication_state": "PUBLISHED_CONFIRMED",
                        "facebook_submission_status": "RECONCILED_VERIFIED",
                        "facebook_verification_method": result.verification_method,
                    },
                )
            elif not result.success and current is not None and current.status is WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED:
                self.repository.transition(
                    job_id,
                    WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
                    event_type="reconciliation_not_found",
                    details={"publish_clicked": False, "reason": "not_found"},
                    data_patch={
                        "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
                        "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
                    },
                )
            return result
        except Exception as exc:
            self.logger.error("Reconciliation failed for job %s: %s", job_id, exc)
            raise
