from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from app.browser.chrome_manager import ChromeManager
from app.browser.facebook_page_state import (
    FacebookDetectionResult, FacebookPageState, FacebookStateDetector,
)
from app.browser.selector_resolver import SelectorResolutionError, SelectorResolver
from app.config.settings import Settings
from app.error_events import safe_browser_url
from app.models.results import (
    FacebookCommentResult,
    FacebookPermalinkResult,
    FacebookPostPreparationResult,
    FacebookPublishResult,
)
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.post_content_service import PostContentService


class FacebookManualActionRequired(RuntimeError):
    pass


class FacebookTransientError(RuntimeError):
    pass


class FacebookPublicationUncertain(RuntimeError):
    pass


class FacebookWebClient:
    """Single async Playwright publisher, informed by the legacy bilingual selectors."""

    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        chrome: ChromeManager,
        *,
        resolver: SelectorResolver | None = None,
        content: PostContentService | None = None,
        state_detector: FacebookStateDetector | None = None,
        confirmation_provider: Callable[[str], str] = input,
        edit_provider: Callable[[], str] | None = None,
        screenshot_selection_provider: Callable[[str], str] = input,
        force_publish: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.chrome = chrome
        self.resolver = resolver or SelectorResolver(settings.selectors_path, save_html=settings.save_diagnostic_html)
        self.content = content or PostContentService(settings)
        self.state_detector = state_detector or FacebookStateDetector(
            timeout_seconds=settings.facebook_state_detection_timeout_seconds,
            probe_timeout_ms=settings.facebook_selector_probe_timeout_ms,
        )
        self.confirmation_provider = confirmation_provider
        self.edit_provider = edit_provider or self._read_multiline_edit
        self.screenshot_selection_provider = screenshot_selection_provider
        self.force_publish = force_publish
        self.logger = logger or logging.getLogger("cdha_pipeline.facebook")
        self._pages: dict[str, Any] = {}

    async def prepare_post(
        self,
        *,
        target_url: str,
        post_text: str,
        image_paths: list[Path],
        job_id: str,
    ) -> FacebookPostPreparationResult:
        target = self.content.normalize_target_url(target_url)
        job = self._require_job(job_id)
        cdha_view_url = str(job.data.get("cdha_view_url") or "")
        self.content.validate_post_text(
            post_text, source_url=job.source_url, cdha_view_url=cdha_view_url
        )
        images = [self.content.validate_image(path) for path in image_paths]
        if not images:
            raise ValueError("At least one validated screenshot is required")
        if len(images) > self.settings.facebook_max_image_count:
            raise ValueError("Facebook image count exceeds configured limit")
        fingerprint = self.content.content_fingerprint(target, post_text, images)
        self._guard_duplicate(job_id, target, fingerprint)
        job = self._require_job(job_id)
        if job.status is WorkflowStatus.FACEBOOK_PUBLISH_FAILED:
            job = self.repository.transition(
                job_id, WorkflowStatus.RETRY_PENDING,
                details={"retry_step": "facebook_prepare"},
            )
        if job.status not in {WorkflowStatus.APPROVED, WorkflowStatus.RETRY_PENDING}:
            raise ValueError(f"Facebook preparation requires APPROVED; got {job.status.value}")
        image_hashes = [self.content.image_sha256(path) for path in images]
        self.repository.transition(
            job_id,
            WorkflowStatus.FACEBOOK_PREPARING,
            details={"target_url": target, "image_count": len(images)},
            data_patch={
                "retain_assets": True,
                "facebook_target_url": target,
                "facebook_target_type": self.settings.facebook_target_type,
                "facebook_post_text": post_text,
                "facebook_image_paths": [str(path) for path in images],
                "facebook_image_sha256": image_hashes,
                "facebook_content_hash": fingerprint,
                "facebook_error": None,
                "facebook_force_override": self.force_publish,
            },
        )
        if self.force_publish:
            self.repository.record_event(
                job_id, details={"manual_override": "force_facebook_publish"}
            )
        diagnostics = self._diagnostics_dir(job_id)
        try:
            page, uploaded = await self._prepare_composer(
                target, post_text, images, job_id, diagnostics
            )
            preview = (self._job_dir(job_id) / "facebook-composer-preview.png").resolve()
            await page.screenshot(path=str(preview), full_page=True)
            self._pages[job_id] = page
            self.repository.transition(
                job_id,
                WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
                details={"preview_screenshot_path": str(preview)},
                data_patch={
                    "facebook_preview_screenshot_path": str(preview),
                    "facebook_uploaded_preview_count": uploaded,
                },
            )
            return FacebookPostPreparationResult(
                True, job_id, target, post_text, [str(path) for path in images],
                uploaded, len(images), str(preview), [], None,
            )
        except FacebookManualActionRequired as exc:
            return FacebookPostPreparationResult(
                False, job_id, target, post_text, [str(path) for path in images],
                0, len(images), error=str(exc),
            )
        except Exception as exc:
            await self._fail_with_diagnostics(
                job_id, None, "composer-open-failure", str(exc),
                allowed={WorkflowStatus.FACEBOOK_PREPARING},
                target=WorkflowStatus.FACEBOOK_PUBLISH_FAILED,
            )
            return FacebookPostPreparationResult(
                False, job_id, target, post_text, [str(path) for path in images],
                0, len(images), error=str(exc),
            )

    async def publish_prepared_post(self, *, job_id: str) -> FacebookPublishResult:
        job = self._require_job(job_id)
        if job.status is not WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW:
            raise ValueError(
                "Facebook publishing requires FACEBOOK_WAITING_FOR_MANUAL_REVIEW; "
                f"got {job.status.value}"
            )
        target = str(job.data.get("facebook_target_url") or "")
        post_text = str(job.data.get("facebook_post_text") or "")
        images = [Path(path) for path in job.data.get("facebook_image_paths") or []]
        self._validate_publish_ready(job, post_text)
        page = self._pages.get(job_id)
        if page is None or getattr(page, "is_closed", lambda: False)():
            page, _ = await self._prepare_composer(
                target, post_text, images, job_id, self._diagnostics_dir(job_id)
            )
        self._display_final_gate(job_id, job.data)
        choice = self.confirmation_provider("Select [1-6]: ").strip()
        if choice != "1":
            if choice == "2":
                self.repository.transition(
                    job_id, WorkflowStatus.APPROVED,
                    details={"facebook_manual_gate": "cancelled"},
                )
            elif choice == "3":
                edited = self.edit_provider().strip()
                self._validate_publish_ready(job, edited)
                path = self.content.write_text_atomic(
                    self._job_dir(job_id) / "facebook_post.txt", edited
                )
                self.repository.update_data(job_id, {
                    "facebook_operator_text": edited,
                    "facebook_post_text": edited,
                    "facebook_post_text_path": str(path),
                })
                self.repository.transition(
                    job_id, WorkflowStatus.APPROVED,
                    details={"facebook_manual_gate": "post_text_edited_reprepare_required"},
                )
            elif choice == "4":
                raw = self.screenshot_selection_provider(
                    "Comma-separated screenshot filenames in desired approved subset: "
                )
                selected = [item.strip() for item in raw.split(",") if item.strip()]
                selected_paths, warnings = self.content.select_screenshots(job_id, selected)
                self.repository.update_data(job_id, {
                    "facebook_selected_screenshot_names": selected,
                    "facebook_image_paths": [str(path) for path in selected_paths],
                    "facebook_image_sha256": [self.content.image_sha256(path) for path in selected_paths],
                    "facebook_screenshot_warnings": warnings,
                })
                self.repository.transition(
                    job_id, WorkflowStatus.APPROVED,
                    details={"facebook_manual_gate": "screenshot_selection_changed_reprepare_required"},
                )
            elif choice == "5":
                self.repository.record_event(
                    job_id, details={"facebook_manual_gate": "resume_later"}
                )
            elif choice == "6":
                preview = Path(str(job.data.get("facebook_preview_screenshot_path") or ""))
                if preview.is_file():
                    subprocess.Popen(
                        ["xdg-open", str(preview)], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, start_new_session=True,
                    )
                self.repository.record_event(
                    job_id, details={"facebook_manual_gate": "preview_opened"}
                )
            else:
                self.repository.record_event(
                    job_id, details={"facebook_manual_gate": f"invalid_selection_{choice}"}
                )
            return FacebookPublishResult(
                False, job_id, target, warnings=["Publication was not approved"],
                error="Operator did not select Publish now",
            )
        privacy_scan = self.content.privacy.scan(post_text)
        self.repository.record_event(
            job_id,
            details={
                "facebook_manual_gate": "publish_approved",
                "privacy_risk_level": privacy_scan.risk_level,
                "privacy_categories": list(privacy_scan.detected_categories),
                "media_pii_warning_acknowledged": True,
            },
        )
        if self.force_publish:
            warning = self.confirmation_provider(
                "FORCE MODE may create a duplicate. Type FORCE PUBLISH to continue: "
            ).strip()
            if warning != "FORCE PUBLISH":
                return FacebookPublishResult(False, job_id, target, error="Force confirmation failed")
        started = datetime.now(UTC)
        before_ids = await self._visible_post_ids(page)
        self.repository.transition(
            job_id,
            WorkflowStatus.FACEBOOK_PUBLISHING,
            details={"publication_started_at": started.isoformat()},
            data_patch={
                "facebook_publication_started_at": started.isoformat(),
                "facebook_known_post_ids": sorted(before_ids),
            },
        )
        publish_clicked = False
        try:
            button = await self.resolver.find_first(
                page, "facebook.publish_button", timeout_ms=10_000,
                diagnostics_dir=self._diagnostics_dir(job_id),
                context=f"job_id={job_id} state=FACEBOOK_PUBLISHING action=publish_exact_button",
            )
            await button.click()
            publish_clicked = True
            
            # Handle two-step publish flow (Tiếp -> Đăng)
            try:
                second_button = await self.resolver.find_first(
                    page, "facebook.post_button", timeout_ms=10_000
                )
                await second_button.click()
            except Exception:
                pass
            result, signals = await self._verify_publication(
                page, job_id, post_text, images, started, before_ids
            )
            if not result.success:
                paths = await self._save_diagnostics(page, job_id, "publish-verification-failure")
                self.repository.transition(
                    job_id,
                    WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
                    details={"verification_signals": signals},
                    data_patch={
                        "facebook_publication_uncertain": True,
                        "facebook_verification_signals": signals,
                        "facebook_diagnostic_screenshot_path": str(paths[0]),
                        "facebook_error": result.error,
                    },
                )
                return result
            completed = datetime.now(UTC)
            self.repository.transition(
                job_id,
                WorkflowStatus.FACEBOOK_PUBLISHED,
                details={"verification_signals": signals},
                data_patch={
                    "facebook_publication_verified": True,
                    "facebook_publication_completed_at": completed.isoformat(),
                    "facebook_verification_signals": signals,
                    "facebook_post_id": result.post_id,
                    "facebook_post_url_candidate": result.post_url,
                    "facebook_error": None,
                },
            )
            return result
        except Exception as exc:
            paths = await self._save_diagnostics(page, job_id, "publish-verification-failure")
            current = self._require_job(job_id)
            if current.status is WorkflowStatus.FACEBOOK_PUBLISHING:
                target_status = (
                    WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
                    if publish_clicked
                    else WorkflowStatus.FACEBOOK_PUBLISH_FAILED
                )
                self.repository.transition(
                    job_id,
                    target_status,
                    event_type=(
                        "FACEBOOK_PUBLICATION_RECONCILIATION_REQUIRED"
                        if publish_clicked
                        else "JOB_STATE_CHANGED"
                    ),
                    details={"error": str(exc), "publish_clicked": publish_clicked},
                    data_patch={
                        "facebook_error": str(exc),
                        "facebook_publication_uncertain": publish_clicked,
                        "facebook_diagnostic_screenshot_path": str(paths[0]),
                    },
                )
            return FacebookPublishResult(
                False, job_id, target,
                diagnostic_screenshot_path=str(paths[0]), error=str(exc),
            )

    async def reconcile_interrupted_publication(self, *, job_id: str) -> FacebookPublishResult:
        """Verify a post after a crash without ever clicking Publish again."""
        job = self._require_job(job_id)
        if job.status is not WorkflowStatus.FACEBOOK_PUBLISHING:
            raise ValueError(f"Publication reconciliation requires FACEBOOK_PUBLISHING; got {job.status.value}")
        target = str(job.data.get("facebook_target_url") or "")
        text = str(job.data.get("facebook_post_text") or "")
        images = [Path(path) for path in job.data.get("facebook_image_paths") or []]
        before_ids = set(job.data.get("facebook_known_post_ids") or [])
        raw_started = str(job.data.get("facebook_publication_started_at") or "")
        started = datetime.fromisoformat(raw_started.replace("Z", "+00:00")) if raw_started else datetime.now(UTC)
        page: Any = None
        try:
            page = await self.chrome.new_page()
            await page.goto(
                target, wait_until="domcontentloaded",
                timeout=self.settings.facebook_navigation_timeout_ms,
            )
            await self._ensure_authenticated(page, job_id, "publication-reconciliation")
            result, signals = await self._verify_publication(
                page, job_id, text, images, started, before_ids
            )
            if result.success and result.post_url:
                self.repository.transition(
                    job_id, WorkflowStatus.FACEBOOK_PUBLISHED,
                    event_type="FACEBOOK_PUBLICATION_RECONCILED",
                    details={"verification_signals": signals, "publish_clicked": False},
                    data_patch={
                        "facebook_publication_verified": True,
                        "facebook_publication_uncertain": False,
                        "facebook_post_id": result.post_id,
                        "facebook_post_url_candidate": result.post_url,
                        "facebook_post_url": result.post_url,
                    },
                )
                return result
            self.repository.transition(
                job_id, WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
                event_type="FACEBOOK_PUBLICATION_RECONCILIATION_REQUIRED",
                details={"verification_signals": signals, "publish_clicked": False},
                data_patch={"facebook_publication_uncertain": True},
            )
            return result
        except Exception as exc:
            current = self._require_job(job_id)
            if current.status is WorkflowStatus.FACEBOOK_PUBLISHING:
                self.repository.transition(
                    job_id, WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
                    event_type="FACEBOOK_PUBLICATION_RECONCILIATION_REQUIRED",
                    details={"error_type": type(exc).__name__, "publish_clicked": False},
                    data_patch={"facebook_publication_uncertain": True},
                )
            return FacebookPublishResult(False, job_id, target, error=str(exc))
        finally:
            if page is not None and not page.is_closed():
                close = getattr(page, "close", None)
                if close is not None:
                    await close()

    async def extract_new_post_permalink(
        self, *, job_id: str, publication_started_at: datetime
    ) -> FacebookPermalinkResult:
        job = self._require_job(job_id)
        if job.status is WorkflowStatus.POST_URL_EXTRACTION_FAILED:
            job = self.repository.transition(
                job_id, WorkflowStatus.RETRY_PENDING,
                details={"retry_step": "facebook_permalink"},
            )
        if job.status not in {WorkflowStatus.FACEBOOK_PUBLISHED, WorkflowStatus.RETRY_PENDING}:
            raise ValueError(f"Permalink extraction cannot run from {job.status.value}")
        self.repository.transition(job_id, WorkflowStatus.POST_URL_EXTRACTING)
        page: Any = None
        try:
            page = await self.chrome.new_page()
            target = str(job.data.get("facebook_target_url") or "")
            await page.goto(target, wait_until="domcontentloaded", timeout=self.settings.page_timeout_seconds*1000)
            await self._ensure_authenticated(page, job_id, "permalink-extraction-failure")
            # Loop to handle Facebook feed cache delays (limited to 30s to avoid bans)
            deadline = time.monotonic() + min(30, self.settings.facebook_post_discovery_timeout_seconds)
            candidate: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                candidate = await self._find_exact_new_post(
                    page,
                    str(job.data.get("facebook_post_text") or ""),
                    publication_started_at,
                    int(job.data.get("facebook_uploaded_preview_count") or 0),
                    set(job.data.get("facebook_known_post_ids") or []),
                )
                if candidate:
                    break
                await asyncio.sleep(5)
                await page.reload(wait_until="domcontentloaded")
                
            if not candidate:
                raise RuntimeError("Exact newly published Facebook post could not be identified after timeout")
            permalink = self.normalize_permalink(candidate["url"], base_url=target)
            post_id = self.extract_post_id(permalink)
            expected_id = str(job.data.get("facebook_post_id") or "")
            if expected_id and post_id and expected_id != post_id:
                raise RuntimeError("Permalink does not refer to the verified post ID")
            self.repository.transition(
                job_id, WorkflowStatus.POST_URL_EXTRACTED,
                details={"extraction_method": candidate.get("method")},
                data_patch={
                    "facebook_post_url": permalink,
                    "facebook_post_id": post_id or expected_id or None,
                    "facebook_permalink_extraction_method": candidate.get("method"),
                },
            )
            return FacebookPermalinkResult(
                True, job_id, permalink, post_id or expected_id or None,
                candidate.get("method"), [], None,
            )
        except Exception as exc:
            if page is not None:
                await self._save_diagnostics(page, job_id, "permalink-extraction-failure")
            current = self._require_job(job_id)
            if current.status is WorkflowStatus.POST_URL_EXTRACTING:
                self.repository.transition(
                    job_id, WorkflowStatus.POST_URL_EXTRACTION_FAILED,
                    details={"error": str(exc)}, data_patch={"facebook_permalink_error": str(exc)},
                )
            return FacebookPermalinkResult(False, job_id, error=str(exc))

    async def add_permalink_comment(
        self, *, post_url: str, comment_text: str, job_id: str, image_path: Path | None = None
    ) -> FacebookCommentResult:
        job = self._require_job(job_id)
        if job.status is WorkflowStatus.COMMENT_FAILED:
            job = self.repository.transition(
                job_id, WorkflowStatus.RETRY_PENDING,
                details={"retry_step": "facebook_comment"},
            )
        if job.status not in {WorkflowStatus.POST_URL_EXTRACTED, WorkflowStatus.RETRY_PENDING}:
            raise ValueError(f"Facebook commenting cannot run from {job.status.value}")
        normalized_url = self.normalize_permalink(post_url, base_url=post_url)
        self.repository.transition(job_id, WorkflowStatus.COMMENT_ADDING)
        page: Any = None
        try:
            existing_result = job.data.get("facebook_comment_result") or {}
            if existing_result.get("success") and self._same_comment(
                existing_result.get("comment_text", ""), comment_text
            ):
                return await self._complete_reused_comment(job_id, normalized_url, comment_text)
            page = await self.chrome.new_page()
            await page.goto(normalized_url, wait_until="domcontentloaded", timeout=self.settings.page_timeout_seconds*1000)
            await self._ensure_authenticated(page, job_id, "comment-failure")
            # For Reels, the comment section might be hidden. Try clicking the comment button first.
            try:
                toggle = page.locator('div[aria-label*="bình luận" i][role="button"], div[aria-label*="comment" i][role="button"]').first
                if await toggle.is_visible(timeout=2000):
                    await toggle.click()
                    await page.wait_for_timeout(2000) # Wait for comments to load
            except Exception:
                pass
                
            comments = await self._all_texts(page, "facebook.visible_comment")
            if any(self._same_comment(value, comment_text) for value in comments):
                return await self._complete_reused_comment(job_id, normalized_url, comment_text)
            
            box = await self.resolver.find_first(
                page, "facebook.comment_input", timeout_ms=10_000,
                diagnostics_dir=self._diagnostics_dir(job_id),
                context=f"job_id={job_id} state=COMMENT_ADDING action=comment_exact_post",
            )
            await box.fill(comment_text)
            if await self._input_text(box) != comment_text:
                raise RuntimeError("Facebook comment insertion verification failed")
            
            if image_path and image_path.exists():
                file_input = page.locator('input[type="file"][accept*="image"]').first
                await file_input.set_input_files(str(image_path))
                await page.wait_for_timeout(5000) # Wait for image to be attached
            try:
                submit = await self.resolver.find_first(page, "facebook.comment_submit", timeout_ms=2_000)
                await submit.click()
            except SelectorResolutionError:
                await box.focus()
                await box.press("Enter")
            await self._wait_for_visible_comment(page, comment_text)
            posted_at = datetime.now(UTC)
            result = FacebookCommentResult(
                True, job_id, normalized_url, None, comment_text, posted_at, [], None, False
            )
            self.repository.transition(
                job_id, WorkflowStatus.COMMENT_ADDED,
                details={"comment_reused": False},
                data_patch={"facebook_comment_result": result.to_dict()},
            )
            self.repository.transition(job_id, WorkflowStatus.COMPLETED)
            return result
        except Exception as exc:
            if page is not None:
                await self._save_diagnostics(page, job_id, "comment-failure")
            current = self._require_job(job_id)
            if current.status is WorkflowStatus.COMMENT_ADDING:
                self.repository.transition(
                    job_id, WorkflowStatus.COMMENT_FAILED,
                    details={"error": str(exc)}, data_patch={"facebook_comment_error": str(exc)},
                )
            return FacebookCommentResult(False, job_id, normalized_url, comment_text=comment_text, error=str(exc))

    async def detect_page_state(self, page: Any) -> FacebookDetectionResult:
        if hasattr(page, "locator") or not isinstance(self.state_detector, FacebookStateDetector):
            return await self.state_detector.detect(page)
        # Compatibility for lightweight unit fakes; real Playwright pages always use
        # the structured detector above.
        if await self.resolver.exists(page, "facebook.login_indicators", timeout_ms=700):
            state = FacebookPageState.LOGIN_REQUIRED
        elif await self.resolver.exists(page, "facebook.checkpoint_indicators", timeout_ms=700):
            state = FacebookPageState.CHECKPOINT
        elif await self.resolver.exists(page, "facebook.authenticated_marker", timeout_ms=1_500):
            state = FacebookPageState.LOGGED_IN
        else:
            state = FacebookPageState.UNKNOWN
        title = await page.title() if hasattr(page, "title") else ""
        return FacebookDetectionResult(
            state=state, probes=(), url=safe_browser_url(str(getattr(page, "url", ""))),
            title=title, frames=(), has_dialog=False, elapsed_ms=0,
        )

    async def is_authenticated(self, page: Any) -> bool:
        return (await self.detect_page_state(page)).state is FacebookPageState.LOGGED_IN

    async def _prepare_composer(
        self, target: str, post_text: str, images: list[Path], job_id: str, diagnostics: Path
    ) -> tuple[Any, int]:
        page = await self.chrome.new_page()
        self._pages[job_id] = page
        await page.goto(target, wait_until="domcontentloaded", timeout=self.settings.page_timeout_seconds*1000)
        await self._ensure_authenticated(page, job_id, "composer-open-failure")
        if await self.resolver.exists(page, "facebook.target_access_denied", timeout_ms=1_200):
            raise RuntimeError("Facebook target is unavailable or posting permission is missing")
        entry = await self.resolver.find_first(
            page, "facebook.create_post_entry", timeout_ms=10_000,
            diagnostics_dir=diagnostics,
            context=f"job_id={job_id} state=FACEBOOK_PREPARING action=open_composer",
        )
        await entry.click()
        await self.resolver.find_first(
            page, "facebook.composer_dialog", timeout_ms=10_000,
            diagnostics_dir=diagnostics,
            context=f"job_id={job_id} state=FACEBOOK_PREPARING action=verify_dialog",
        )
        textbox = await self.resolver.find_first(
            page, "facebook.composer_textbox", timeout_ms=10_000,
            diagnostics_dir=diagnostics,
            context=f"job_id={job_id} state=FACEBOOK_PREPARING action=insert_text",
        )
        await textbox.fill(post_text)
        if await self._input_text(textbox) != post_text:
            raise RuntimeError("Facebook composer text read-back did not match approved content")
        file_input = await self.resolver.find_first(
            page, "facebook.file_input", visible=False, timeout_ms=10_000,
            diagnostics_dir=diagnostics,
            context=f"job_id={job_id} state=FACEBOOK_PREPARING action=upload_images",
        )
        try:
            await file_input.set_input_files([str(path) for path in images])
            uploaded = await self._wait_for_uploads(page, len(images))
        except Exception:
            await self._save_diagnostics(page, job_id, "image-upload-failure")
            raise
        # Just ensure the publish button is present in the DOM
        button = await self.resolver.find_first(
            page, "facebook.publish_button", timeout_ms=10_000,
            diagnostics_dir=diagnostics,
            context=f"job_id={job_id} state=FACEBOOK_PREPARING action=verify_publish_ready",
        )
        return page, uploaded

    async def _ensure_authenticated(self, page: Any, job_id: str, diagnostic_name: str) -> None:
        detection = await self.detect_page_state(page)
        state = detection.state
        self.repository.record_event(job_id, details={
            "event_type": "FACEBOOK_STATE_DETECTED",
            "detected_state": state.value,
            "url": detection.url,
            "elapsed_ms": detection.elapsed_ms,
        })
        if state is FacebookPageState.LOGGED_IN:
            return

        data_patch: dict[str, Any] = {"facebook_page_state": state.value}
        if state is FacebookPageState.UNKNOWN and self.settings.facebook_save_debug_artifacts:
            folder = await self.state_detector.save_unknown_artifacts(
                page, detection,
                root=self.settings.project_root / "runtime" / "debug" / "facebook",
                job_id=job_id,
                browser_profile=str(self.settings.chrome_profile_dir),
                browser_port=9222,
            )
            data_patch.update({
                "facebook_debug_screenshot_path": str(folder / "screenshot.png"),
                "facebook_debug_html_path": str(folder / "page.html"),
                "facebook_debug_metadata_path": str(folder / "metadata.json"),
            })

        current = self._require_job(job_id)
        auth_states = {
            FacebookPageState.LOGIN_REQUIRED,
            FacebookPageState.SESSION_EXPIRED,
            FacebookPageState.CHECKPOINT,
            FacebookPageState.TWO_FACTOR,
            FacebookPageState.CONSENT_DIALOG,
        }
        if state in auth_states:
            event_type = (
                "FACEBOOK_CHECKPOINT_DETECTED"
                if state in {FacebookPageState.CHECKPOINT, FacebookPageState.TWO_FACTOR}
                else "FACEBOOK_AUTH_REQUIRED"
            )
            if current.status is WorkflowStatus.FACEBOOK_PREPARING:
                self.repository.transition(
                    job_id, WorkflowStatus.WAITING_FOR_AUTH_REVIEW,
                    event_type=event_type,
                    details={"detected_state": state.value},
                    data_patch=data_patch,
                )
            raise FacebookManualActionRequired(
                f"Facebook requires manual authentication review: {state.value}"
            )
        if state is FacebookPageState.ACCOUNT_DISABLED:
            if current.status is WorkflowStatus.FACEBOOK_PREPARING:
                self.repository.transition(
                    job_id, WorkflowStatus.BLOCKED,
                    event_type="FACEBOOK_ACCOUNT_DISABLED",
                    details={"severity": "critical"},
                    data_patch=data_patch,
                )
            raise FacebookManualActionRequired("Facebook account is disabled; automatic retry is blocked")

        event_type = "FACEBOOK_UNKNOWN_PAGE" if state is FacebookPageState.UNKNOWN else "PLAYWRIGHT_RETRY_SCHEDULED"
        if current.status is WorkflowStatus.FACEBOOK_PREPARING:
            self.repository.transition(
                job_id, WorkflowStatus.RETRYABLE,
                event_type=event_type,
                details={"detected_state": state.value},
                data_patch=data_patch,
            )
        raise FacebookTransientError(f"Transient Facebook page state: {state.value}")

    async def _wait_for_uploads(self, page: Any, expected: int) -> int:
        deadline = time.monotonic() + self.settings.facebook_upload_timeout_seconds
        while time.monotonic() < deadline:
            if await self.resolver.exists(page, "facebook.upload_error", timeout_ms=400):
                raise RuntimeError("Facebook reported an image upload error")
            progress = await self.resolver.exists(page, "facebook.upload_progress", timeout_ms=300)
            count = await self._count_all(page, "facebook.uploaded_preview")
            if not progress and count >= expected:
                return count
            await asyncio.sleep(0.5)
        raise TimeoutError(
            f"Facebook upload previews did not reach expected count {expected} before timeout"
        )

    async def _verify_publication(
        self, page: Any, job_id: str, text: str, images: list[Path],
        started: datetime, before_ids: set[str]
    ) -> tuple[FacebookPublishResult, dict[str, Any]]:
        deadline = time.monotonic() + self.settings.facebook_publish_timeout_seconds
        signals: dict[str, Any] = {}
        candidate: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            signals["success_notification"] = await self.resolver.exists(
                page, "facebook.publish_success", timeout_ms=400
            )
            signals["composer_closed"] = not await self.resolver.exists(
                page, "facebook.composer_dialog", timeout_ms=300
            )
            candidate = await self._find_exact_new_post(
                page, text, started, len(images), before_ids
            )
            signals["exact_post_match"] = bool(candidate)
            signals["text_match"] = bool(candidate and candidate.get("text_match"))
            signals["recent_timestamp"] = bool(candidate and candidate.get("recent"))
            signals["image_count_match"] = bool(
                candidate and candidate.get("image_count") == len(images)
            )
            if self.publication_is_verified(signals):
                url = candidate.get("url") if candidate else None
                post_id = self.extract_post_id(url or "")
                methods = [key for key, value in signals.items() if value]
                return FacebookPublishResult(
                    True, job_id, self._require_job(job_id).data.get("facebook_target_url", ""),
                    post_id, url, datetime.now(UTC), "+".join(methods), None, [], None,
                ), signals
            await asyncio.sleep(1)
        return FacebookPublishResult(
            False, job_id, self._require_job(job_id).data.get("facebook_target_url", ""),
            warnings=["Publication could not be verified strongly enough"],
            error="Facebook publication outcome is uncertain",
        ), signals

    @staticmethod
    def publication_is_verified(signals: dict[str, Any]) -> bool:
        strong = bool(signals.get("success_notification") or signals.get("exact_post_match"))
        supporting = sum(
            bool(signals.get(key))
            for key in ("composer_closed", "text_match", "recent_timestamp", "image_count_match")
        )
        return strong and supporting >= 1

    async def _find_exact_new_post(
        self, page: Any, approved_text: str, started: datetime,
        expected_images: int, before_ids: set[str]
    ) -> dict[str, Any] | None:
        articles = await self._all_locators(page, "facebook.feed_post")
        normalized_approved = self._normalize_text(approved_text)
        best: tuple[int, dict[str, Any]] | None = None
        for article in articles:
            try:
                body = self._normalize_text(await article.inner_text())
                # Use lenient matching to account for "Xem thêm" (See more) truncation
                prefix = normalized_approved[:50]
                text_match = normalized_approved in body or prefix in body
                links = await self._article_links(article)
                for href in links:
                    try:
                        url = self.normalize_permalink(href, base_url=str(page.url))
                    except ValueError:
                        continue
                    post_id = self.extract_post_id(url) or ""
                    is_new = not post_id or post_id not in before_ids
                    images = await article.locator("img[src]").count()
                    recent = await self._article_is_recent(article, started)
                    score = 4 * text_match + 2 * is_new + int(recent) + int(images == expected_images)
                    candidate = {
                        "url": url, "post_id": post_id, "text_match": text_match,
                        "recent": recent, "image_count": images,
                        "method": "content+new-id+timestamp+image-count",
                    }
                    if text_match and is_new and (best is None or score > best[0]):
                        best = (score, candidate)
            except Exception:
                continue
        return best[1] if best else None

    async def _article_links(self, article: Any) -> list[str]:
        links: list[str] = []
        for candidate in self.resolver.candidates("facebook.post_timestamp_link"):
            try:
                locator = self.resolver._locator(article, candidate)
                for index in range(await locator.count()):
                    href = await locator.nth(index).get_attribute("href")
                    if href:
                        links.append(href)
            except Exception:
                continue
        return links

    async def _article_is_recent(self, article: Any, started: datetime) -> bool:
        try:
            raw = await article.locator("time").first.get_attribute("datetime")
            if raw:
                value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return value >= started - timedelta(minutes=5)
        except Exception:
            pass
        return False

    @classmethod
    def normalize_permalink(cls, href: str, *, base_url: str) -> str:
        absolute = urljoin(base_url, str(href or "").strip())
        parsed = urlsplit(absolute)
        host = (parsed.hostname or "").lower()
        if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
            raise ValueError("Permalink is not a Facebook URL")
        path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        allowed_query: dict[str, str] = {}
        if path.endswith("/story.php") or path == "/story.php":
            if query.get("story_fbid"):
                allowed_query["story_fbid"] = query["story_fbid"]
            if query.get("id"):
                allowed_query["id"] = query["id"]
        elif path.endswith("/photo.php") or path == "/photo.php" or path.endswith("/photo") or path == "/photo":
            if query.get("fbid"):
                allowed_query["fbid"] = query["fbid"]
        valid_path = bool(re.search(
            r"/(?:posts|permalink|reel|share/(?:v|p))/[^/]+$|/groups/[^/]+/posts/[^/]+$", path
        ))
        valid_query = bool(allowed_query)
        if not (valid_path or valid_query):
            raise ValueError("Facebook URL is not a supported post permalink")
        return urlunsplit(("https", "www.facebook.com", path, urlencode(allowed_query), ""))

    @staticmethod
    def extract_post_id(url: str) -> str | None:
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query))
        for key in ("story_fbid", "fbid"):
            if query.get(key):
                return query[key]
        match = re.search(r"/(?:posts|permalink|reel)/([^/?]+)$", parsed.path.rstrip("/"))
        return match.group(1) if match else None

    async def _visible_post_ids(self, page: Any) -> set[str]:
        ids: set[str] = set()
        for article in await self._all_locators(page, "facebook.feed_post"):
            for href in await self._article_links(article):
                try:
                    post_id = self.extract_post_id(self.normalize_permalink(href, base_url=str(page.url)))
                    if post_id:
                        ids.add(post_id)
                except ValueError:
                    continue
        return ids

    def _guard_duplicate(self, job_id: str, target: str, fingerprint: str) -> None:
        duplicates = self.repository.find_facebook_duplicates(
            fingerprint, target, exclude_job_id=job_id
        )
        for duplicate in duplicates:
            verified = bool(
                duplicate.data.get("facebook_publication_verified")
                or duplicate.data.get("facebook_post_url")
            )
            uncertain = bool(
                duplicate.status is WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
                or duplicate.data.get("facebook_publication_uncertain")
            )
            if (verified or uncertain) and not self.force_publish:
                kind = "uncertain prior publication" if uncertain else "verified existing post"
                raise ValueError(f"Duplicate Facebook publication blocked: {kind} in job {duplicate.job_id}")

    async def _complete_reused_comment(
        self, job_id: str, post_url: str, comment_text: str
    ) -> FacebookCommentResult:
        result = FacebookCommentResult(
            True, job_id, post_url, None, comment_text, datetime.now(UTC),
            ["Matching permalink comment already exists"], None, True,
        )
        self.repository.transition(
            job_id, WorkflowStatus.COMMENT_ADDED,
            details={"comment_reused": True},
            data_patch={"facebook_comment_result": result.to_dict()},
        )
        self.repository.transition(job_id, WorkflowStatus.COMPLETED)
        return result

    async def _wait_for_visible_comment(self, page: Any, text: str) -> None:
        deadline = time.monotonic() + self.settings.facebook_publish_timeout_seconds
        while time.monotonic() < deadline:
            if any(self._same_comment(value, text) for value in await self._all_texts(page, "facebook.visible_comment")):
                return
            if await self.resolver.exists(page, "facebook.error_banner", timeout_ms=300):
                raise RuntimeError("Facebook displayed an error while posting the comment")
            await asyncio.sleep(0.5)
        raise TimeoutError("Facebook comment did not appear before timeout")

    async def _count_all(self, page: Any, key: str) -> int:
        total = 0
        for candidate in self.resolver.candidates(key):
            try:
                total = max(total, await self.resolver._locator(page, candidate).count())
            except Exception:
                continue
        return total

    async def _all_locators(self, page: Any, key: str) -> list[Any]:
        values: list[Any] = []
        for candidate in self.resolver.candidates(key):
            try:
                locator = self.resolver._locator(page, candidate)
                count = await locator.count()
                if count:
                    return [locator.nth(i) for i in range(count)]
            except Exception:
                continue
        return values

    async def _all_texts(self, page: Any, key: str) -> list[str]:
        values: list[str] = []
        for locator in await self._all_locators(page, key):
            try:
                values.append((await locator.inner_text()).strip())
            except Exception:
                continue
        return values

    async def _save_diagnostics(self, page: Any, job_id: str, name: str) -> tuple[Path, Path]:
        folder = self._diagnostics_dir(job_id)
        folder.mkdir(parents=True, exist_ok=True)
        screenshot = (folder / f"{name}.png").resolve()
        metadata = (folder / f"{name}.json").resolve()
        await page.screenshot(path=str(screenshot), full_page=True)
        metadata.write_text(
            json.dumps({"url": safe_browser_url(str(page.url)), "name": name}, indent=2),
            encoding="utf-8",
        )
        artifact = metadata
        if self.settings.save_diagnostic_html:
            artifact = (folder / f"{name}.html").resolve()
            artifact.write_text(await page.content(), encoding="utf-8")
        for path in (screenshot, metadata, artifact):
            path.chmod(0o600)
        return screenshot, artifact

    async def _fail_with_diagnostics(
        self, job_id: str, page: Any | None, name: str, error: str,
        *, allowed: set[WorkflowStatus], target: WorkflowStatus
    ) -> None:
        paths: tuple[Path, Path] | None = None
        actual_page = page or self._pages.get(job_id)
        if actual_page is not None:
            paths = await self._save_diagnostics(actual_page, job_id, name)
        current = self._require_job(job_id)
        if current.status in allowed:
            patch: dict[str, Any] = {"facebook_error": error}
            if paths:
                patch["facebook_failure_screenshot_path"] = str(paths[0])
                patch["facebook_failure_diagnostic_path"] = str(paths[1])
            self.repository.transition(
                job_id, target, details={"error": error}, data_patch=patch
            )

    def _display_final_gate(self, job_id: str, data: dict[str, Any]) -> None:
        print("=" * 50)
        print("FACEBOOK POST READY")
        print("=" * 50)
        print(f"Job ID: {job_id}")
        print(f"Target: {data.get('facebook_target_url') or ''}")
        post_text = str(data.get("facebook_post_text") or "")
        privacy_scan = self.content.privacy.scan(post_text)
        print(f"Post text path: {data.get('facebook_post_text_path') or ''}")
        print("Final post text:")
        print(post_text)
        print(f"Approved image order: {data.get('facebook_image_paths') or []}")
        print(f"Expected images: {len(data.get('facebook_image_paths') or [])}")
        print(f"Uploaded previews: {data.get('facebook_uploaded_preview_count') or 0}")
        print(f"Preview screenshot: {data.get('facebook_preview_screenshot_path') or ''}")
        print(f"Source Reel: {data.get('source_url') or self._source_url(job_id)}")
        print(f"CDHA view URL: {data.get('cdha_view_url') or ''}")
        print(f"CDHA result: {data.get('cdha_result_json_path') or ''}")
        print(f"Content fingerprint: {data.get('facebook_content_hash') or ''}")
        print(f"Privacy scan: risk={privacy_scan.risk_level}, categories={list(privacy_scan.detected_categories)}")
        for warning in privacy_scan.warnings:
            print(f"PRIVACY WARNING: {warning}")
        print("[1] Publish now\n[2] Cancel and keep APPROVED\n[3] Edit post text")
        print("[4] Change screenshot selection\n[5] Save and resume later\n[6] Open preview screenshot")

    @staticmethod
    def _read_multiline_edit() -> str:
        print("Paste the complete Facebook post text. Enter a single '.' line to finish:")
        lines: list[str] = []
        while True:
            line = input()
            if line == ".":
                return "\n".join(lines)
            lines.append(line)

    def _source_url(self, job_id: str) -> str:
        job = self._require_job(job_id)
        return job.source_url

    def _validate_publish_ready(self, job: Any, post_text: str) -> None:
        cdha = job.data.get("cdha_result") or {}
        self.content.validate_publish_ready(
            post_text,
            source_url=job.source_url,
            cdha_view_url=str(
                cdha.get("analysis_url") or job.data.get("cdha_view_url") or ""
            ),
            key_findings=list(cdha.get("key_findings") or []),
            impression=cdha.get("impression"),
        )

    def _require_job(self, job_id: str) -> Any:
        job = self.repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        return job

    def _job_dir(self, job_id: str) -> Path:
        return (self.settings.job_data_dir / job_id).resolve()

    def _diagnostics_dir(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "browser_snapshots" / "facebook"

    @staticmethod
    async def _input_text(locator: Any) -> str:
        try:
            return await locator.input_value()
        except Exception:
            return (await locator.text_content() or "").strip()

    @staticmethod
    def _normalize_text(value: str) -> str:
        cleaned = re.sub(r'[^\w\s]', '', str(value or ""))
        return " ".join(cleaned.split()).casefold()

    @classmethod
    def _same_comment(cls, left: str, right: str) -> bool:
        norm_left = cls._normalize_text(left)
        norm_right = cls._normalize_text(right)
        if norm_left == norm_right:
            return True
        if len(norm_right) > 0:
            prefix = norm_right[:30]
            if prefix in norm_left:
                return True
        if norm_right.startswith("chi tiết:") and norm_left.startswith("chi tiết:"):
            return True
        if norm_right.startswith("copy link chia sẻ:") and norm_left.startswith("copy link chia sẻ:"):
            return True
        return False
