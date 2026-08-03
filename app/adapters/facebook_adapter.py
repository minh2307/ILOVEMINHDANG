from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from app.browser.facebook_client import FacebookWebClient
from app.config.settings import Settings
from app.models.results import (
    FacebookCommentResult,
    FacebookPermalinkResult,
    FacebookPostPreparationResult,
    FacebookPublishResult,
    FacebookWorkflowResult,
)
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.post_content_service import PostContentService


class FacebookPublisherAdapter:
    """Phase 5-compatible boundary backed by Phase 4 SQLite state and artifacts."""

    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        client: FacebookWebClient,
        content: PostContentService | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.client = client
        self.content = content or PostContentService(settings)

    async def prepare(self, *, job_id: str) -> FacebookPostPreparationResult:
        job = self._job(job_id)
        if job.status not in {
            WorkflowStatus.APPROVED,
            WorkflowStatus.FACEBOOK_PUBLISH_FAILED,
            WorkflowStatus.RETRY_PENDING,
        }:
            raise ValueError(f"Facebook prepare requires APPROVED; got {job.status.value}")
        target_url = self.settings.effective_facebook_target_url()
        if not target_url:
            raise ValueError("FACEBOOK_TARGET_URL or FACEBOOK_TEST_TARGET_URL is required")
        cdha = job.data.get("cdha_result") or {}
        post_text = self.content.build_post(
            source_url=job.source_url,
            key_findings=list(cdha.get("key_findings") or []),
            impression=cdha.get("impression"),
            clinical_factors=str(job.data.get("clinical_factors") or ""),
            operator_text=job.data.get("facebook_operator_text"),
            cdha_view_url=str(job.data.get("cdha_view_url") or ""),
        )
        selected_names = job.data.get("facebook_selected_screenshot_names")
        images, warnings = self.content.select_screenshots(
            job_id, list(selected_names) if selected_names else None
        )
        post_path = self.content.write_text_atomic(
            self.settings.job_data_dir / job_id / "facebook_post.txt", post_text
        )
        self.repository.update_data(
            job_id,
            {
                "facebook_post_text_path": str(post_path),
                "facebook_post_text": post_text,
                "facebook_screenshot_warnings": warnings,
            },
        )
        result = await self.client.prepare_post(
            target_url=target_url,
            post_text=post_text,
            image_paths=images,
            job_id=job_id,
        )
        combined = [*warnings, *result.warnings]
        if combined != result.warnings:
            result = replace(result, warnings=combined)
        return result

    async def publish(self, *, job_id: str) -> FacebookPublishResult:
        return await self.client.publish_prepared_post(job_id=job_id)

    async def reconcile_publication(self, *, job_id: str) -> FacebookPublishResult:
        return await self.client.reconcile_interrupted_publication(job_id=job_id)

    async def extract_permalink(self, *, job_id: str) -> FacebookPermalinkResult:
        job = self._job(job_id)
        raw_started = job.data.get("facebook_publication_started_at")
        if not raw_started:
            raise ValueError("Facebook publication start time is missing")
        started = datetime.fromisoformat(str(raw_started).replace("Z", "+00:00"))
        return await self.client.extract_new_post_permalink(
            job_id=job_id, publication_started_at=started
        )

    async def add_permalink_comment(self, *, job_id: str) -> FacebookCommentResult:
        job = self._job(job_id)
        post_url = str(job.data.get("facebook_post_url") or "")
        if not post_url:
            raise ValueError("Exact Facebook post permalink is missing")
        comment = self.content.build_permalink_comment(post_url)
        comment_path = self.content.write_text_atomic(
            self.settings.job_data_dir / job_id / "facebook_comment.txt", comment
        )
        self.repository.update_data(
            job_id, {"facebook_comment_text_path": str(comment_path), "facebook_comment_text": comment}
        )
        if not self.settings.facebook_comment_enabled or (
            self.settings.test_mode and self.settings.test_mode_disable_comment
        ):
            self.repository.transition(job_id, WorkflowStatus.COMMENT_ADDING)
            result = FacebookCommentResult(
                True, job_id, post_url, comment_text=comment,
                posted_at=datetime.now(UTC),
                warnings=["Facebook permalink comments are disabled by configuration or test mode"],
                reused=True,
            )
            self.repository.transition(
                job_id, WorkflowStatus.COMMENT_ADDED,
                details={"comment_disabled": True},
                data_patch={"facebook_comment_result": result.to_dict()},
            )
            self.repository.transition(job_id, WorkflowStatus.COMPLETED)
            return result
        image_path = self.settings.job_data_dir / job_id / "screenshots" / "01-detailed-analysis.png"
        return await self.client.add_permalink_comment(
            post_url=post_url, comment_text=comment, job_id=job_id, image_path=image_path
        )

    async def complete(self, *, job_id: str) -> FacebookWorkflowResult:
        job = self._job(job_id)
        warnings: list[str] = []
        if job.status is WorkflowStatus.FACEBOOK_PUBLISHING:
            reconciled = await self.reconcile_publication(job_id=job_id)
            warnings.extend(reconciled.warnings)
            if not reconciled.success:
                return self._workflow_result(job_id, False, warnings, reconciled.error)
            job = self._job(job_id)
        if job.status in {
            WorkflowStatus.APPROVED,
            WorkflowStatus.FACEBOOK_PUBLISH_FAILED,
        }:
            prepared = await self.prepare(job_id=job_id)
            warnings.extend(prepared.warnings)
            if not prepared.success:
                return self._workflow_result(job_id, False, warnings, prepared.error)
            job = self._job(job_id)
        if job.status is WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW:
            published = await self.publish(job_id=job_id)
            warnings.extend(published.warnings)
            if not published.success:
                return self._workflow_result(job_id, False, warnings, published.error)
            job = self._job(job_id)
        if job.status in {
            WorkflowStatus.FACEBOOK_PUBLISHED,
            WorkflowStatus.POST_URL_EXTRACTION_FAILED,
        }:
            permalink = await self.extract_permalink(job_id=job_id)
            warnings.extend(permalink.warnings)
            if not permalink.success:
                return self._workflow_result(job_id, False, warnings, permalink.error)
            job = self._job(job_id)
        if job.status in {WorkflowStatus.POST_URL_EXTRACTED, WorkflowStatus.COMMENT_FAILED}:
            comment = await self.add_permalink_comment(job_id=job_id)
            warnings.extend(comment.warnings)
            if not comment.success:
                return self._workflow_result(job_id, False, warnings, comment.error)
        return self._workflow_result(
            job_id, self._job(job_id).status is WorkflowStatus.COMPLETED, warnings, None
        )

    def _workflow_result(
        self, job_id: str, success: bool, warnings: list[str], error: str | None
    ) -> FacebookWorkflowResult:
        job = self._job(job_id)
        comment = job.data.get("facebook_comment_result") or {}
        return FacebookWorkflowResult(
            success, job_id, job.status.value, job.data.get("facebook_post_url"),
            bool(comment.get("reused")), warnings, error,
        )

    def _job(self, job_id: str):
        job = self.repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        return job
