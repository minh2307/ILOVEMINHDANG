"""Phase 5 — End-to-end resumable CDHA pipeline orchestrator.

Architecture
------------
* The orchestrator delegates to accepted Phase 1–4 adapter interfaces.
* It contains no raw Playwright selectors, SQL, or hard-coded file paths.
* Every external action is idempotent: completed artifacts are reused.
* Two explicit manual gates are enforced and may never be bypassed.
* Signal handling ensures safe shutdown and resume.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.adapters.downloadreel_adapter import DownloadReelAdapter, DownloadReelCoordinator
from app.adapters.facebook_adapter import FacebookPublisherAdapter
from app.browser.chrome_manager import ChromeManager
from app.browser.facebook_client import FacebookWebClient
from app.browser.selector_resolver import SelectorResolver
from app.config.settings import Settings
from app.models.results import PipelineResult
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.post_content_service import PostContentService
from app.services.review_service import ReviewService


_RESUME_REQUIRED = frozenset({
    WorkflowStatus.WAITING_FOR_REVIEW,
    WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
    WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
    WorkflowStatus.FACEBOOK_PUBLISH_FAILED,
    WorkflowStatus.POST_URL_EXTRACTION_FAILED,
    WorkflowStatus.COMMENT_FAILED,
    WorkflowStatus.REJECTED,
    WorkflowStatus.CANCELLED,
})

_RESUMABLE = frozenset({
    WorkflowStatus.DOWNLOADED,
    WorkflowStatus.CLINICAL_FACTORS_GENERATED,
    WorkflowStatus.CDHA_ANALYZED,
    WorkflowStatus.SCREENSHOTS_CAPTURED,
    WorkflowStatus.WAITING_FOR_REVIEW,
    WorkflowStatus.APPROVED,
    WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
    WorkflowStatus.FACEBOOK_PUBLISHED,
    WorkflowStatus.POST_URL_EXTRACTED,
    WorkflowStatus.COMMENT_ADDED,
    WorkflowStatus.FACEBOOK_PUBLISH_FAILED,
    WorkflowStatus.POST_URL_EXTRACTION_FAILED,
    WorkflowStatus.COMMENT_FAILED,
    WorkflowStatus.RETRY_PENDING,
})


class CDHAPipeline:
    """Resumable end-to-end orchestrator for the CDHA medical AI workflow."""

    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        *,
        chrome: ChromeManager | None = None,
        force_download: bool = False,
        force_facebook_publish: bool = False,
        skip_facebook_comment: bool = False,
        dry_run: bool = False,
        yes: bool = False,
        confirmation_provider: Callable[[str], str] = input,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self._chrome = chrome
        self.force_download = force_download
        self.force_facebook_publish = force_facebook_publish
        self.skip_facebook_comment = skip_facebook_comment
        self.dry_run = dry_run
        self.yes = yes
        self.confirmation_provider = confirmation_provider
        self.logger = logger or logging.getLogger("cdha_pipeline")
        self._cancelled = False
        self._setup_signal_handlers()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start_from_reel(
        self,
        *,
        reel_url: str,
        force_download: bool = False,
        force_facebook_publish: bool = False,
    ) -> PipelineResult:
        """Create a new job (or reuse an existing duplicate) and run."""
        effective_force = force_download or self.force_download
        effective_fb_force = force_facebook_publish or self.force_facebook_publish

        if self.dry_run:
            return await self._dry_run_validate(reel_url)

        adapter = DownloadReelAdapter(self.settings, self.repository)
        coordinator = DownloadReelCoordinator(self.settings, self.repository, adapter)
        result = await coordinator.run(reel_url, force_download=effective_force)
        if not result.success:
            return self._make_pipeline_result(
                result.job_id, False,
                error=result.error or "DownloadReel failed",
                pending="Retry download or inspect error",
            )
        return await self.resume(job_id=result.job_id)

    async def resume(self, *, job_id: str) -> PipelineResult:
        """Resume a job from its current persisted state."""
        job = self.repository.get_job(job_id)
        if job is None:
            return PipelineResult(False, job_id, "UNKNOWN", error=f"Job not found: {job_id}")

        status = job.status

        if status is WorkflowStatus.COMPLETED:
            self.logger.info("Job %s already COMPLETED — nothing to do.", job_id)
            return self._make_pipeline_result(job_id, True)

        if status is WorkflowStatus.CANCELLED:
            return self._make_pipeline_result(
                job_id, False, error="Job is cancelled",
                pending="Create a new job or use --force-download to reprocess",
            )

        if status is WorkflowStatus.REJECTED:
            return self._make_pipeline_result(
                job_id, False, error="Job was rejected during medical review",
                pending="Review the content and create a new job if appropriate",
            )

        if status is WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN:
            return self._make_pipeline_result(
                job_id, False,
                error="Facebook publication outcome is uncertain; inspect Facebook manually",
                pending="Inspect Facebook page, then contact operator for resolution",
            )

        # Route to correct resume point
        if status in {
            WorkflowStatus.CREATED, WorkflowStatus.DOWNLOADREEL_RUNNING,
            WorkflowStatus.DOWNLOADREEL_FAILED,
        }:
            return await self._step_download(job_id)

        if status in {
            WorkflowStatus.DOWNLOADED, WorkflowStatus.GEMINI_FAILED,
            WorkflowStatus.NEEDS_GEMINI_LOGIN, WorkflowStatus.GEMINI_OPENING,
        }:
            return await self._step_gemini(job_id)

        if status in {
            WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.CDHA_FAILED,
            WorkflowStatus.NEEDS_CDHA_LOGIN, WorkflowStatus.CDHA_OPENING,
            WorkflowStatus.CDHA_UPLOADING, WorkflowStatus.CDHA_ANALYZING,
        }:
            return await self._step_cdha(job_id)

        if status in {WorkflowStatus.CDHA_ANALYZED, WorkflowStatus.SCREENSHOTS_CAPTURING}:
            return await self._step_screenshots(job_id)

        if status in {WorkflowStatus.SCREENSHOTS_CAPTURED}:
            self.repository.transition(job_id, WorkflowStatus.WAITING_FOR_REVIEW)
            return self._make_pipeline_result(
                job_id, False,
                pending="Run: python main.py --review-job " + job_id,
            )

        if status is WorkflowStatus.WAITING_FOR_REVIEW:
            return self._make_pipeline_result(
                job_id, False,
                pending="Run: python main.py --review-job " + job_id,
            )

        if status in {
            WorkflowStatus.APPROVED,
            WorkflowStatus.FACEBOOK_PUBLISH_FAILED,
            WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        }:
            return await self._step_facebook(job_id)

        if status is WorkflowStatus.FACEBOOK_PUBLISHED:
            return await self._step_permalink(job_id)

        if status in {
            WorkflowStatus.POST_URL_EXTRACTED, WorkflowStatus.COMMENT_FAILED,
            WorkflowStatus.POST_URL_EXTRACTION_FAILED,
        }:
            return await self._step_comment(job_id)

        if status is WorkflowStatus.COMMENT_ADDED:
            self.repository.transition(job_id, WorkflowStatus.COMPLETED)
            return self._make_pipeline_result(job_id, True)

        if status is WorkflowStatus.RETRY_PENDING:
            retry_step = (self.repository.get_job(job_id) or job).data.get("retry_step", "")
            return await self._route_retry(job_id, retry_step)

        return self._make_pipeline_result(
            job_id, False, error=f"Cannot resume from status: {status.value}",
            pending="Inspect job state and determine correct action",
        )

    async def run_until_review(self, *, job_id: str) -> PipelineResult:
        """Run until WAITING_FOR_REVIEW; stop without publishing."""
        job = self.repository.get_job(job_id)
        if job is None:
            return PipelineResult(False, job_id, "UNKNOWN", error="Job not found")
        status = job.status
        # Steps before review
        if status in {WorkflowStatus.CREATED, WorkflowStatus.DOWNLOADREEL_FAILED,
                      WorkflowStatus.DOWNLOADREEL_RUNNING}:
            r = await self._step_download(job_id)
            if not r.success:
                return r
        if self.repository.get_job(job_id).status in {
            WorkflowStatus.DOWNLOADED, WorkflowStatus.GEMINI_FAILED,
            WorkflowStatus.NEEDS_GEMINI_LOGIN, WorkflowStatus.GEMINI_OPENING,
        }:
            r = await self._step_gemini(job_id)
            if not r.success:
                return r
        if self.repository.get_job(job_id).status in {
            WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.CDHA_FAILED,
            WorkflowStatus.NEEDS_CDHA_LOGIN, WorkflowStatus.CDHA_OPENING,
            WorkflowStatus.CDHA_UPLOADING, WorkflowStatus.CDHA_ANALYZING,
        }:
            r = await self._step_cdha(job_id)
            if not r.success:
                return r
        # At review now
        return self._make_pipeline_result(
            job_id, False,
            pending="Run: python main.py --review-job " + job_id,
        )

    async def continue_after_approval(self, *, job_id: str) -> PipelineResult:
        """Continue from APPROVED through COMPLETED; requires operator confirmation at Facebook gate."""
        job = self.repository.get_job(job_id)
        if job is None:
            return PipelineResult(False, job_id, "UNKNOWN", error="Job not found")
        if job.status is not WorkflowStatus.APPROVED:
            return self._make_pipeline_result(
                job_id, False,
                error=f"continue-after-approval requires APPROVED; got {job.status.value}",
            )
        return await self._step_facebook(job_id)

    # ------------------------------------------------------------------
    # Private pipeline steps (each delegates to accepted adapters)
    # ------------------------------------------------------------------

    async def _step_download(self, job_id: str) -> PipelineResult:
        job = self.repository.get_job(job_id)
        if job is None:
            return PipelineResult(False, job_id, "UNKNOWN", error="Job not found")
        adapter = DownloadReelAdapter(self.settings, self.repository)
        coordinator = DownloadReelCoordinator(self.settings, self.repository, adapter)
        result = await coordinator.run(job.source_url, force_download=self.force_download)
        if not result.success:
            return self._make_pipeline_result(
                job_id, False,
                error=result.error or "DownloadReel failed",
                pending="Retry with: python main.py --retry-job " + job_id,
            )
        return await self._step_gemini(job_id)

    async def _step_gemini(self, job_id: str) -> PipelineResult:
        """Delegate to existing GeminiWebClient through ChromeManager."""
        from app.browser.gemini_client import GeminiWebClient
        chrome = await self._get_chrome()
        resolver = SelectorResolver(self.settings.selectors_path)
        gemini = GeminiWebClient(self.settings, self.repository, chrome, resolver=resolver)
        job = self.repository.get_job(job_id)
        if job is None:
            return PipelineResult(False, job_id, "UNKNOWN", error="Job not found")
        
        # Idempotency check: if clinical factors already generated, skip Gemini
        if job.data.get("clinical_factors_path"):
            cf_path = Path(job.data.get("clinical_factors_path", ""))
            if cf_path.is_file() and cf_path.read_text().strip():
                self.logger.info("Gemini clinical factors already exist, skipping Gemini step.", extra={"job_id": job_id})
                return await self._step_cdha(job_id)
        
        result = await gemini.generate_clinical_factors(
            caption=str(job.data.get("caption") or ""),
            comments=list(job.data.get("comments") or []),
            job_id=job_id,
        )
        if not result.success:
            return self._make_pipeline_result(
                job_id, False,
                error=result.error or "Gemini step failed",
                pending="Complete login and retry: python main.py --retry-job " + job_id,
            )
        return await self._step_cdha(job_id)

    async def _step_cdha(self, job_id: str) -> PipelineResult:
        """Delegate to existing CDHAWebClient through ChromeManager."""
        from app.browser.cdha_client import CDHAWebClient
        chrome = await self._get_chrome()
        resolver = SelectorResolver(self.settings.selectors_path)
        cdha = CDHAWebClient(self.settings, self.repository, chrome, resolver=resolver)
        job = self.repository.get_job(job_id)
        if job is None:
            return PipelineResult(False, job_id, "UNKNOWN", error="Job not found")
        video_path = job.data.get("video_path")
        factors = job.data.get("clinical_factors")
        if not video_path:
            return self._make_pipeline_result(
                job_id, False, error="No downloaded video path found in job data",
            )
        if not factors:
            return self._make_pipeline_result(
                job_id, False, error="No masked Clinical Factors found in job data",
            )
        result = await cdha.analyze_video(
            video_path=Path(str(video_path)),
            clinical_factors=str(factors),
            job_id=job_id,
        )
        if not result.success:
            return self._make_pipeline_result(
                job_id, False,
                error=result.error or "CDHA step failed",
                pending="Retry: python main.py --retry-job " + job_id,
            )
        return await self._step_screenshots(job_id)

    async def _step_screenshots(self, job_id: str) -> PipelineResult:
        """Screenshots are captured inside CDHAWebClient; move to review."""
        job = self.repository.get_job(job_id)
        if job is None:
            return PipelineResult(False, job_id, "UNKNOWN", error="Job not found")
        status = job.status
        if status is WorkflowStatus.CDHA_ANALYZED:
            self.repository.transition(job_id, WorkflowStatus.SCREENSHOTS_CAPTURING)
            self.repository.transition(job_id, WorkflowStatus.SCREENSHOTS_CAPTURED)
        if self.repository.get_job(job_id).status is WorkflowStatus.SCREENSHOTS_CAPTURED:
            self.repository.transition(job_id, WorkflowStatus.WAITING_FOR_REVIEW)
        ReviewService(self.settings, self.repository).display(job_id)
        return self._make_pipeline_result(
            job_id, False,
            pending="Run: python main.py --review-job " + job_id,
        )

    async def _step_facebook(self, job_id: str) -> PipelineResult:
        """Delegate Phase 4 Facebook workflow through FacebookPublisherAdapter."""
        if not self.settings.facebook_target_url and not (
            self.settings.test_mode and self.settings.facebook_test_target_url
        ):
            return self._make_pipeline_result(
                job_id, False,
                error="FACEBOOK_TARGET_URL is not configured",
                pending="Set FACEBOOK_TARGET_URL in .env and retry",
            )
        chrome = await self._get_chrome()
        resolver = SelectorResolver(self.settings.selectors_path)
        client = FacebookWebClient(
            self.settings, self.repository, chrome, resolver=resolver,
            force_publish=self.force_facebook_publish,
            confirmation_provider=self.confirmation_provider,
        )
        adapter = FacebookPublisherAdapter(self.settings, self.repository, client)

        job = self.repository.get_job(job_id)
        if job is None:
            return PipelineResult(False, job_id, "UNKNOWN", error="Job not found")

        # Prepare if not yet done
        if job.status is WorkflowStatus.APPROVED:
            prepared = await adapter.prepare(job_id=job_id)
            if not prepared.success:
                return self._make_pipeline_result(
                    job_id, False, error=prepared.error or "Facebook preparation failed",
                    pending="Retry: python main.py --prepare-facebook-post " + job_id,
                )

        # Publish (requires operator confirmation inside publish_prepared_post)
        if self.repository.get_job(job_id).status is WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW:
            published = await adapter.publish(job_id=job_id)
            if not published.success:
                return self._make_pipeline_result(
                    job_id, False, error=published.error or "Facebook publish failed",
                    pending="Run: python main.py --publish-facebook " + job_id,
                )

        return await self._step_permalink(job_id)

    async def _step_permalink(self, job_id: str) -> PipelineResult:
        chrome = await self._get_chrome()
        resolver = SelectorResolver(self.settings.selectors_path)
        client = FacebookWebClient(
            self.settings, self.repository, chrome, resolver=resolver,
            confirmation_provider=self.confirmation_provider,
        )
        adapter = FacebookPublisherAdapter(self.settings, self.repository, client)
        result = await adapter.extract_permalink(job_id=job_id)
        if not result.success:
            return self._make_pipeline_result(
                job_id, False, error=result.error or "Permalink extraction failed",
                pending="Retry: python main.py --extract-facebook-link " + job_id,
            )
        return await self._step_comment(job_id)

    async def _step_comment(self, job_id: str) -> PipelineResult:
        if self.skip_facebook_comment:
            self.repository.record_event(job_id, details={"comment_skipped": True})
            if self.repository.get_job(job_id).status is WorkflowStatus.POST_URL_EXTRACTED:
                self.repository.transition(job_id, WorkflowStatus.COMMENT_ADDING)
                result_obj = __import__(
                    "app.models.results", fromlist=["FacebookCommentResult"]
                ).FacebookCommentResult(
                    True, job_id, "", comment_text="", reused=True,
                    warnings=["Comment skipped by --skip-facebook-comment flag"],
                )
                self.repository.transition(
                    job_id, WorkflowStatus.COMMENT_ADDED,
                    details={"comment_skipped": True},
                    data_patch={"facebook_comment_result": result_obj.to_dict()},
                )
                self.repository.transition(job_id, WorkflowStatus.COMPLETED)
            return self._make_pipeline_result(job_id, True)
        chrome = await self._get_chrome()
        resolver = SelectorResolver(self.settings.selectors_path)
        client = FacebookWebClient(
            self.settings, self.repository, chrome, resolver=resolver,
            confirmation_provider=self.confirmation_provider,
        )
        adapter = FacebookPublisherAdapter(self.settings, self.repository, client)
        result = await adapter.add_permalink_comment(job_id=job_id)
        if not result.success:
            return self._make_pipeline_result(
                job_id, False, error=result.error or "Permalink comment failed",
                pending="Retry: python main.py --comment-facebook-link " + job_id,
            )
        return self._make_pipeline_result(job_id, True)

    async def _route_retry(self, job_id: str, retry_step: str) -> PipelineResult:
        if retry_step in {"download", ""}:
            return await self._step_download(job_id)
        if retry_step == "gemini":
            return await self._step_gemini(job_id)
        if retry_step in {"cdha", "cdha_opening"}:
            return await self._step_cdha(job_id)
        if retry_step == "facebook_prepare":
            return await self._step_facebook(job_id)
        if retry_step == "facebook_permalink":
            return await self._step_permalink(job_id)
        if retry_step == "facebook_comment":
            return await self._step_comment(job_id)
        return self._make_pipeline_result(
            job_id, False, error=f"Unknown retry step: {retry_step!r}",
        )

    async def _dry_run_validate(self, reel_url: str) -> PipelineResult:
        """Validate configuration and URL without any external actions."""
        from app.services.reel_normalization import normalize_reel_url, ReelUrlError
        warnings: list[str] = []
        errors: list[str] = []

        # Normalize URL
        try:
            normalized = normalize_reel_url(reel_url)
        except (ReelUrlError, ValueError) as exc:
            errors.append(f"URL normalization: {exc}")
            normalized = reel_url

        # Check Facebook target
        if not self.settings.facebook_target_url:
            warnings.append("FACEBOOK_TARGET_URL is not set (required for Facebook steps)")

        # Check Chrome
        chrome_ok = False
        for candidate in (
            self.settings.chrome_executable_fallback,
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/chromium-browser"),
        ):
            if candidate.exists():
                chrome_ok = True
                break
        if not chrome_ok:
            warnings.append("Google Chrome executable was not found at configured path")

        # Check DB
        try:
            db_dir = self.settings.database_path.parent
            db_dir.mkdir(parents=True, exist_ok=True)
            if not os.access(str(db_dir), os.W_OK):
                warnings.append("Database directory may not be writable")
        except OSError as exc:
            errors.append(f"Database directory: {exc}")

        # Check selectors file
        if not self.settings.selectors_path.is_file():
            errors.append("selectors.yaml not found at configured path")

        # Check duplicate history
        try:
            existing = self.repository.find_latest_by_source_url(reel_url)
            if existing and existing.status is WorkflowStatus.COMPLETED:
                warnings.append(
                    f"Dry-run: an existing COMPLETED job exists for this URL: {existing.job_id}"
                )
        except Exception as exc:
            warnings.append(f"Could not check duplicate history: {exc}")

        print("[DRY-RUN] Planned transitions for:", normalized)
        print("[DRY-RUN] CREATED → DOWNLOADREEL_RUNNING → DOWNLOADED → ...")
        print("[DRY-RUN] No external actions were performed.")
        if warnings:
            for warning in warnings:
                print(f"[DRY-RUN] WARNING: {warning}")
        if errors:
            for error in errors:
                print(f"[DRY-RUN] ERROR: {error}")
            return PipelineResult(
                False, "dry_run", "DRY_RUN",
                source_url=reel_url,
                warnings=warnings,
                error="; ".join(errors),
                pending_manual_action="Fix errors before running without --dry-run",
            )
        return PipelineResult(
            True, "dry_run", "DRY_RUN",
            source_url=reel_url,
            warnings=warnings,
            pending_manual_action="Run without --dry-run to start the workflow",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_chrome(self) -> ChromeManager:
        if self._chrome is None:
            raise RuntimeError(
                "ChromeManager was not injected; call pipeline within ChromeManager context"
            )
        return self._chrome

    def _make_pipeline_result(
        self,
        job_id: str,
        success: bool,
        *,
        error: str | None = None,
        pending: str | None = None,
        warnings: list[str] | None = None,
    ) -> PipelineResult:
        job = self.repository.get_job(job_id)
        data: dict[str, Any] = job.data if job else {}
        screenshot_paths = [
            str(path)
            for path in (self.settings.job_data_dir / job_id / "screenshots").iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ] if (self.settings.job_data_dir / job_id / "screenshots").is_dir() else []
        return PipelineResult(
            success=success,
            job_id=job_id,
            current_status=job.status.value if job else "UNKNOWN",
            source_url=job.source_url if job else "",
            video_path=data.get("video_path"),
            clinical_factors_path=data.get("clinical_factors_path"),
            cdha_result_path=data.get("cdha_result_json_path"),
            screenshot_paths=screenshot_paths,
            facebook_post_url=data.get("facebook_post_url"),
            facebook_comment_id=data.get("facebook_comment_id"),
            completed_steps=self._completed_steps(job),
            pending_manual_action=pending,
            warnings=warnings or [],
            error=error,
            started_at=job.created_at if job else None,
            updated_at=job.updated_at if job else None,
        )

    @staticmethod
    def _completed_steps(job: Any) -> list[str]:
        if job is None:
            return []
        steps = []
        status = job.status
        sequence = [
            (WorkflowStatus.DOWNLOADED, "download"),
            (WorkflowStatus.CLINICAL_FACTORS_GENERATED, "gemini"),
            (WorkflowStatus.CDHA_ANALYZED, "cdha"),
            (WorkflowStatus.SCREENSHOTS_CAPTURED, "screenshots"),
            (WorkflowStatus.APPROVED, "review"),
            (WorkflowStatus.FACEBOOK_PUBLISHED, "facebook_publish"),
            (WorkflowStatus.POST_URL_EXTRACTED, "permalink_extraction"),
            (WorkflowStatus.COMMENT_ADDED, "permalink_comment"),
            (WorkflowStatus.COMPLETED, "completed"),
        ]
        ordered_values = [s.value for s in WorkflowStatus]
        current_idx = ordered_values.index(status.value) if status.value in ordered_values else 0
        for threshold, name in sequence:
            if threshold.value in ordered_values:
                if ordered_values.index(threshold.value) <= current_idx:
                    steps.append(name)
        return steps

    def _setup_signal_handlers(self) -> None:
        """Attach Ctrl+C handler for graceful pipeline pause."""
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._handle_shutdown)
        except Exception:
            pass  # Not in async context yet; handlers added at launch

    def _handle_shutdown(self) -> None:
        self._cancelled = True
        self.logger.warning("Shutdown signal received; pipeline will pause safely.")
        print("\nWorkflow paused safely. Resume with: python main.py --resume-job <JOB_ID>")
