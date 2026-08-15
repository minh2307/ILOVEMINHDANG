from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import json
import logging
import re
import subprocess
import time
import unicodedata
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
from app.domain.policies.external_side_effect_policy import (
    facebook_submission_is_committed,
    repository_facebook_submission_evidence,
    verified_permalink,
)


class FacebookManualActionRequired(RuntimeError):
    pass


class FacebookTransientError(RuntimeError):
    pass


class FacebookPublicationUncertain(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PublishSubmitResult:
    submitted: bool
    posting_indicator_detected: bool = False
    posting_indicator_cleared: bool = False
    composer_closed: bool = False
    success_notification: bool = False
    interstitial_detected: bool = False
    interstitial_dismissed: bool = False
    elapsed_seconds: float = 0.0

    def to_signals(self) -> dict[str, bool | float]:
        return {
            "submitted": self.submitted,
            "posting_indicator_detected": self.posting_indicator_detected,
            "posting_indicator_cleared": self.posting_indicator_cleared,
            "composer_closed": self.composer_closed,
            "success_notification": self.success_notification,
            "interstitial_detected": self.interstitial_detected,
            "interstitial_dismissed": self.interstitial_dismissed,
            "settle_elapsed_seconds": self.elapsed_seconds,
        }


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
        self._interstitial_detected_jobs: set[str] = set()
        self._interstitial_dismissed_jobs: set[str] = set()
        self._interstitial_diagnostic_jobs: set[str] = set()
        self._candidate_observations: set[tuple[str, str, str]] = set()
        self._reload_feed_during_verification = False

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
        if repository_facebook_submission_evidence(
            self.repository, job_id, job.data
        ).committed:
            raise ValueError(
                "Facebook publication was already submitted; run reconciliation instead of preparing another post"
            )
        cdha_view_url = str(job.data.get("cdha_view_url") or "")
        self.content.validate_post_text(
            post_text, source_url=job.source_url, cdha_view_url=cdha_view_url
        )
        images = [self.content.validate_image(path) for path in image_paths]
        if not images:
            raise ValueError("At least one validated screenshot is required")
        if len(images) > self.settings.facebook_max_image_count:
            raise ValueError("Facebook image count exceeds configured limit")
        fingerprint = self.content.content_fingerprint(
            target, post_text, images, job_id, job.source_url, cdha_view_url
        )
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
        
        attempt_id = self.repository.create_publication_attempt(
            job_id=job_id,
            content_fingerprint=fingerprint,
            target_url=target,
            status="CREATED",
            media_hashes=image_hashes,
        )
        self.repository.update_data(job_id, {"facebook_attempt_id": attempt_id})
        
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
            self.repository.update_data(job_id, {
                "facebook_submission_status": "FAILED_BEFORE_SUBMIT",
                "facebook_publication_state": "FAILED_BEFORE_SUBMIT",
            })
            return FacebookPostPreparationResult(
                False, job_id, target, post_text, [str(path) for path in images],
                0, len(images), error=str(exc),
            )

    async def publish_prepared_post(self, *, job_id: str) -> FacebookPublishResult:
        job = self._require_job(job_id)
        if repository_facebook_submission_evidence(
            self.repository, job_id, job.data
        ).committed:
            raise ValueError(
                "Facebook publication was already submitted; a second Publish is permanently blocked"
            )
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
        if self.settings.facebook_final_confirmation:
            self._display_final_gate(job_id, job.data)
            choice = self.confirmation_provider("Select [1-6]: ").strip()
        else:
            choice = "1"
            self.repository.record_event(
                job_id, details={"facebook_manual_gate": "disabled_by_configuration"}
            )
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
                success=False,
                status="PUBLISH_CANCELLED",
                target_url=target,
                job_id=job_id,
                warnings=["Publication was not approved"],
                error="Operator did not select Publish now",
            )
        privacy_scan = self.content.privacy.scan(post_text)
        self.repository.record_event(
            job_id,
            details={
                "facebook_manual_gate": "publish_approved",
                "privacy_risk_level": privacy_scan.risk_level,
                "privacy_categories": list(privacy_scan.detected_categories),
                "media_pii_warning_acknowledged": self.settings.facebook_final_confirmation,
            },
        )
        if self.force_publish:
            warning = self.confirmation_provider(
                "FORCE MODE may create a duplicate. Type FORCE PUBLISH to continue: "
            ).strip()
            if warning != "FORCE PUBLISH":
                return FacebookPublishResult(
                    success=False,
                    status="FORCE_CONFIRMATION_FAILED",
                    target_url=target,
                    job_id=job_id,
                    error="Force confirmation failed",
                )
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
        submit_checkpointed = False
        attempt_id = job.data.get("facebook_attempt_id")
        try:
            composer_dialog = await self.resolver.find_first(
                page, "facebook.composer_dialog", timeout_ms=10_000,
                diagnostics_dir=self._diagnostics_dir(job_id),
                context=f"job_id={job_id} state=FACEBOOK_PUBLISHING action=scope_composer",
            )
            button = await self.resolver.find_first(
                composer_dialog, "facebook.next_button", timeout_ms=10_000,
                context=f"job_id={job_id} state=FACEBOOK_PUBLISHING action=next_exact_button",
            )
            
            await self._save_diagnostics(page, job_id, "pre-publish")

            # On Facebook Pages this first action is "Tiếp"/"Next". It only
            # opens post settings; it is not a publication attempt yet.
            await button.click()
            await self._save_diagnostics(page, job_id, "post-next")

            # "Đăng ngay" is a status value inside the scheduling settings
            # row. Clicking that text opens the calendar. Require it to be
            # visible, but never click it or any scheduling control.
            composer_dialog = await self.resolver.find_first(
                page, "facebook.composer_dialog", timeout_ms=10_000
            )
            await self.resolver.find_first(
                composer_dialog, "facebook.publish_now_indicator", timeout_ms=10_000
            )
            final_post_button = await self.resolver.find_first(
                composer_dialog, "facebook.post_button", timeout_ms=10_000
            )

            # Persist SUBMITTING immediately before the real final Post click.
            submitted_at = datetime.now(UTC)
            marker = getattr(self.repository, "mark_facebook_submitting", None)
            if callable(marker):
                marker(
                    job_id,
                    submitted_at=submitted_at.isoformat(),
                    content_fingerprint=str(
                        job.data.get("facebook_content_hash") or ""
                    ),
                    target_url=target,
                )
            else:
                if attempt_id:
                    self.repository.update_publication_attempt(
                        attempt_id, status="SUBMITTING"
                    )
                self.repository.update_data(job_id, {
                    "facebook_submission_status": "SUBMITTING",
                    "facebook_publication_state": "SUBMITTING",
                    "facebook_submitted_at": submitted_at.isoformat(),
                    "facebook_submit_url": safe_browser_url(str(page.url)),
                    "facebook_submit_timestamp": submitted_at.isoformat(),
                })
                self.repository.record_event(
                    job_id, event_type="FACEBOOK_SUBMITTING",
                    details={
                        "browser_url": safe_browser_url(str(page.url)),
                        "timestamp": submitted_at.isoformat(),
                        "submitted_at": submitted_at.isoformat(),
                        "submission_status": "SUBMITTING",
                    }
                )
            submit_checkpointed = True

            await final_post_button.click()
            publish_clicked = True

            if attempt_id:
                self.repository.update_publication_attempt(
                    attempt_id, status="SUBMITTED_UNCONFIRMED"
                )

            self.repository.update_data(job_id, {
                "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
                "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
                "facebook_click_timestamp": submitted_at.isoformat(),
                "facebook_submitted_at": submitted_at.isoformat(),
            })
            self.repository.record_event(
                job_id, event_type="FACEBOOK_SUBMITTED_UNCONFIRMED",
                details={"timestamp": submitted_at.isoformat(), "submission_status": "SUBMITTED_UNCONFIRMED"}
            )
            self._record_publish_milestone(
                job_id,
                "publish_button_clicked",
                submitted_at=submitted_at.isoformat(),
                state_before="SUBMITTING",
                state_after="SUBMITTED_UNCONFIRMED",
            )

            settled = await self._wait_for_publish_to_settle(
                page, job_id=job_id, submitted_at=submitted_at
            )
            self.repository.update_data(
                job_id, {"facebook_publish_settle_signals": settled.to_signals()}
            )
            await self._save_diagnostics(page, job_id, "post-click")

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
                        "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
                        "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
                    },
                )
                self._record_publish_milestone(
                    job_id,
                    "publish_submitted_unconfirmed",
                    submitted_at=submitted_at.isoformat(),
                    elapsed_seconds=(datetime.now(UTC) - submitted_at).total_seconds(),
                    state_before="FACEBOOK_PUBLISHING",
                    state_after="FACEBOOK_PUBLISH_UNCERTAIN",
                )
                if attempt_id:
                    self.repository.update_publication_attempt(
                        attempt_id, status="UNCERTAIN", error_message=result.error
                    )
                return FacebookPublishResult(
                    success=False,
                    status="PUBLICATION_UNCERTAIN",
                    target_url=target,
                    job_id=job_id,
                    diagnostics={"verification_signals": signals},
                    diagnostic_screenshot_path=str(paths[0]),
                    error=result.error or "Facebook publication outcome is uncertain — reconciliation required",
                )
            # Verified publication — require post_id or permalink
            if not (result.post_id or result.permalink):
                # Publication verified by signals but no durable identifier
                paths = await self._save_diagnostics(page, job_id, "publish-no-id")
                self.repository.transition(
                    job_id,
                    WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
                    details={"reason": "verified_signals_but_no_post_id", "verification_signals": signals},
                    data_patch={
                        "facebook_publication_uncertain": True,
                        "facebook_error": "Post published but no verifiable post ID or permalink captured",
                        "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
                        "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
                    },
                )
                return FacebookPublishResult(
                    success=False,
                    status="VERIFICATION_TIMEOUT",
                    target_url=target,
                    job_id=job_id,
                    diagnostics={"verification_signals": signals},
                    error="Post published but no verifiable post ID or permalink captured — reconciliation required",
                )
            completed = datetime.now(UTC)
            permalink = result.permalink or result.post_url
            self.repository.transition(
                job_id,
                WorkflowStatus.FACEBOOK_PUBLISHED,
                details={"verification_signals": signals},
                data_patch={
                    "facebook_publication_verified": True,
                    "facebook_publication_completed_at": completed.isoformat(),
                    "facebook_verification_signals": signals,
                    "facebook_post_id": result.post_id,
                    "facebook_post_url": permalink,
                    "facebook_post_url_candidate": permalink,
                    "facebook_error": None,
                    "facebook_submission_status": "VERIFIED",
                    "facebook_publication_state": "PUBLISHED_CONFIRMED",
                    "facebook_verification_method": result.verification_method,
                    "facebook_verified_at": completed.isoformat(),
                },
            )
            self._record_publish_milestone(
                job_id,
                "post_confirmed",
                submitted_at=submitted_at.isoformat(),
                elapsed_seconds=(completed - submitted_at).total_seconds(),
                candidate_url=permalink,
                match_reason=result.verification_method,
                state_before="SUBMITTED_UNCONFIRMED",
                state_after="PUBLISHED_CONFIRMED",
            )
            if attempt_id:
                self.repository.update_publication_attempt(
                    attempt_id,
                    status="VERIFIED",
                    post_id=result.post_id,
                    permalink=permalink,
                    verification_method=result.verification_method,
                    completed=True,
                )
            # Update the returned result to include the attempt_id
            return replace(result, attempt_id=attempt_id) if attempt_id else result
        except Exception as exc:
            paths = await self._save_diagnostics(page, job_id, "publish-verification-failure")
            current = self._require_job(job_id)
            if current.status is WorkflowStatus.FACEBOOK_PUBLISHING:
                target_status = (
                    WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
                    if publish_clicked or submit_checkpointed
                    else WorkflowStatus.FACEBOOK_PUBLISH_FAILED
                )
                self.repository.transition(
                    job_id,
                    target_status,
                    event_type=(
                        "FACEBOOK_PUBLICATION_RECONCILIATION_REQUIRED"
                        if publish_clicked or submit_checkpointed
                        else "JOB_STATE_CHANGED"
                    ),
                    details={
                        "error": str(exc),
                        "publish_clicked": publish_clicked,
                        "submit_checkpointed": submit_checkpointed,
                    },
                    data_patch={
                        "facebook_error": str(exc),
                        "facebook_publication_uncertain": publish_clicked or submit_checkpointed,
                        "facebook_diagnostic_screenshot_path": str(paths[0]),
                        "facebook_submission_status": (
                            "SUBMITTED_UNCONFIRMED" if publish_clicked or submit_checkpointed else "FAILED_BEFORE_SUBMIT"
                        ),
                        "facebook_publication_state": (
                            "SUBMITTED_UNCONFIRMED" if publish_clicked or submit_checkpointed else "FAILED_BEFORE_SUBMIT"
                        ),
                    },
                )
                if attempt_id:
                    self.repository.update_publication_attempt(
                        attempt_id,
                        status="UNCERTAIN" if publish_clicked or submit_checkpointed else "FAILED",
                        error_message=str(exc),
                        diagnostic_paths=[str(paths[0])] if paths else None,
                        completed=not (publish_clicked or submit_checkpointed),
                    )
            return FacebookPublishResult(
                success=False,
                status="PUBLICATION_UNCERTAIN" if publish_clicked or submit_checkpointed else "PUBLISH_ACTION_FAILED",
                target_url=target,
                job_id=job_id,
                diagnostic_screenshot_path=str(paths[0]),
                diagnostics={
                    "exception": str(exc),
                    "publish_clicked": publish_clicked,
                    "submit_checkpointed": submit_checkpointed,
                },
                error=str(exc),
            )

    async def reconcile_interrupted_publication(self, *, job_id: str) -> FacebookPublishResult:
        """Verify a post after a crash without ever clicking Publish again."""
        job = self._require_job(job_id)
        allowed_reconcile_statuses = {
            WorkflowStatus.FACEBOOK_PUBLISHING,
            WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED,
            WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
            WorkflowStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED,
        }
        if job.status not in allowed_reconcile_statuses:
            raise ValueError(
                f"Publication reconciliation requires one of "
                f"{[s.value for s in allowed_reconcile_statuses]}; got {job.status.value}"
            )
        target = str(job.data.get("facebook_target_url") or "")
        text = str(job.data.get("facebook_post_text") or "")
        images = [Path(path) for path in job.data.get("facebook_image_paths") or []]
        before_ids = set(job.data.get("facebook_known_post_ids") or [])
        raw_started = str(
            job.data.get("facebook_submitted_at")
            or job.data.get("facebook_click_timestamp")
            or job.data.get("facebook_publication_started_at")
            or ""
        )
        started = datetime.fromisoformat(raw_started.replace("Z", "+00:00")) if raw_started else datetime.now(UTC)
        reconciliation_attempt = max(
            int(job.reconciliation_attempts or 0),
            int(job.data.get("facebook_reconciliation_attempt") or 0),
        )
        if reconciliation_attempt < 1:
            begin = getattr(self.repository, "begin_facebook_reconciliation", None)
            if callable(begin):
                reconciliation_attempt = begin(job_id)
            else:
                reconciliation_attempt = 1
        reconciliation_started_at = datetime.now(UTC)
        self._record_publish_milestone(
            job_id,
            "reconciliation_started",
            submitted_at=started.isoformat(),
            attempt=reconciliation_attempt,
            state_before=job.status.value,
            state_after=WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED.value,
            persist=False,
        )
        page: Any = None
        try:
            page = await self.chrome.new_page()
            await page.goto(
                target, wait_until="domcontentloaded",
                timeout=self.settings.facebook_navigation_timeout_ms,
            )
            await self._ensure_authenticated(page, job_id, "publication-reconciliation")
            self._reload_feed_during_verification = True
            try:
                result, signals = await self._verify_publication(
                    page, job_id, text, images, started, before_ids
                )
            finally:
                self._reload_feed_during_verification = False
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
            if result.success and (result.post_id or result.permalink or result.post_url):
                permalink = result.permalink or result.post_url
                verified_at = datetime.now(UTC)
                self.repository.transition(
                    job_id, WorkflowStatus.FACEBOOK_PUBLISHED,
                    event_type="FACEBOOK_PUBLICATION_RECONCILED",
                    details={"verification_signals": signals, "publish_clicked": False},
                    data_patch={
                        "facebook_publication_verified": True,
                        "facebook_publication_uncertain": False,
                        "facebook_post_id": result.post_id,
                        "facebook_post_url_candidate": permalink,
                        "facebook_post_url": permalink,
                        "facebook_submission_status": "RECONCILED_VERIFIED",
                        "facebook_publication_state": "PUBLISHED_CONFIRMED",
                        "facebook_verification_method": result.verification_method,
                        "facebook_verified_at": verified_at.isoformat(),
                        "facebook_reconciliation_exhausted": False,
                    },
                )
                self._record_publish_milestone(
                    job_id,
                    "post_confirmed",
                    submitted_at=started.isoformat(),
                    attempt=reconciliation_attempt,
                    elapsed_seconds=(verified_at - started).total_seconds(),
                    candidate_url=permalink,
                    match_reason=result.verification_method,
                    state_before=WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED.value,
                    state_after="PUBLISHED_CONFIRMED",
                )
                attempt_id = job.data.get("facebook_attempt_id")
                if attempt_id:
                    self.repository.update_publication_attempt(
                        attempt_id,
                        status="VERIFIED",
                        post_id=result.post_id,
                        permalink=permalink,
                        verification_method=result.verification_method,
                        completed=True,
                    )
                return replace(result, attempt_id=attempt_id) if attempt_id else result
            diagnostic_paths = await self._save_diagnostics(
                page, job_id, f"reconciliation-attempt-{reconciliation_attempt}-not-found"
            )
            exhausted = (
                reconciliation_attempt
                >= self.settings.max_facebook_reconciliation_retries
            )
            target_state = (
                WorkflowStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED
                if exhausted
                else WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
            )
            delay = self._reconciliation_retry_delay(reconciliation_attempt)
            event_type = (
                "reconciliation_exhausted"
                if exhausted
                else "reconciliation_retry_scheduled"
            )
            self.repository.transition(
                job_id, target_state,
                event_type=event_type,
                details={
                    "verification_signals": signals,
                    "publish_clicked": False,
                    "reason": "not_found",
                    "attempt": reconciliation_attempt,
                    "max_attempts": self.settings.max_facebook_reconciliation_retries,
                    "delay_seconds": None if exhausted else delay,
                    "state_before": WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED.value,
                    "state_after": target_state.value,
                },
                data_patch={
                    "facebook_publication_uncertain": True,
                    "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
                    "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
                    "facebook_reconciliation_exhausted": exhausted,
                    "facebook_reconciliation_diagnostic_path": str(diagnostic_paths[0]),
                    "facebook_reconciliation_last_error": "not_found",
                    "facebook_reconciliation_next_delay_seconds": None if exhausted else delay,
                },
            )
            self._record_publish_milestone(
                job_id,
                event_type,
                persist=False,
                submitted_at=started.isoformat(),
                attempt=reconciliation_attempt,
                elapsed_seconds=(datetime.now(UTC) - reconciliation_started_at).total_seconds(),
                match_reason="not_found",
                state_before=WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED.value,
                state_after=target_state.value,
            )
            attempt_id = job.data.get("facebook_attempt_id")
            if attempt_id:
                self.repository.update_publication_attempt(
                    attempt_id,
                    status="FAILED" if target_state == WorkflowStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED else "UNCERTAIN",
                    completed=target_state == WorkflowStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED,
                )
            return FacebookPublishResult(
                success=False,
                status=(
                    "RECONCILIATION_EXHAUSTED"
                    if exhausted
                    else "RECONCILIATION_RETRY_SCHEDULED"
                ),
                target_url=target,
                job_id=job_id,
                diagnostics={"verification_signals": signals},
                diagnostic_screenshot_path=str(diagnostic_paths[0]),
                error=(
                    "Reconciliation attempts exhausted without a matching verified post"
                    if exhausted
                    else "Reconciliation did not find a matching verified post yet"
                ),
            )
        except Exception as exc:
            current = self._require_job(job_id)
            if current.status in allowed_reconcile_statuses:
                exhausted = (
                    reconciliation_attempt
                    >= self.settings.max_facebook_reconciliation_retries
                )
                target_state = (
                    WorkflowStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED
                    if exhausted
                    else WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
                )
                diagnostic_path: str | None = None
                if page is not None:
                    paths = await self._save_diagnostics(
                        page,
                        job_id,
                        f"reconciliation-attempt-{reconciliation_attempt}-error",
                    )
                    diagnostic_path = str(paths[0])
                event_type = (
                    "reconciliation_exhausted"
                    if exhausted
                    else "reconciliation_retry_scheduled"
                )
                self.repository.transition(
                    job_id, target_state,
                    event_type=event_type,
                    details={
                        "error_type": type(exc).__name__,
                        "publish_clicked": False,
                        "attempt": reconciliation_attempt,
                        "state_before": current.status.value,
                        "state_after": target_state.value,
                    },
                    data_patch={
                        "facebook_publication_uncertain": True,
                        "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
                        "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
                        "facebook_reconciliation_exhausted": exhausted,
                        "facebook_reconciliation_diagnostic_path": diagnostic_path,
                        "facebook_reconciliation_last_error": type(exc).__name__,
                    },
                )
                self._record_publish_milestone(
                    job_id,
                    event_type,
                    persist=False,
                    submitted_at=started.isoformat(),
                    attempt=reconciliation_attempt,
                    elapsed_seconds=(
                        datetime.now(UTC) - reconciliation_started_at
                    ).total_seconds(),
                    match_reason=type(exc).__name__,
                    state_before=current.status.value,
                    state_after=target_state.value,
                )
            return FacebookPublishResult(
                success=False,
                status=(
                    "RECONCILIATION_EXHAUSTED"
                    if reconciliation_attempt
                    >= self.settings.max_facebook_reconciliation_retries
                    else "RECONCILIATION_RETRY_SCHEDULED"
                ),
                target_url=target,
                job_id=job_id,
                error=str(exc),
            )
        finally:
            if page is not None and not page.is_closed():
                close = getattr(page, "close", None)
                if close is not None:
                    await close()

    def _reconciliation_retry_delay(self, attempt: int) -> float:
        return min(
            self.settings.retry_max_delay_seconds,
            self.settings.retry_initial_delay_seconds
            * (self.settings.retry_multiplier ** max(0, attempt - 1)),
        )

    async def extract_new_post_permalink(
        self, *, job_id: str, publication_started_at: datetime
    ) -> FacebookPermalinkResult:
        job = self._require_job(job_id)
        persisted_permalink = verified_permalink(job.data)
        if persisted_permalink:
            try:
                canonical = self.normalize_permalink(
                    persisted_permalink, base_url=persisted_permalink
                )
            except ValueError as exc:
                return FacebookPermalinkResult(
                    False,
                    job_id,
                    error=f"Persisted verified permalink is invalid: {exc}",
                )
            post_id = self.extract_post_id(canonical)
            expected_id = str(job.data.get("facebook_post_id") or "").strip()
            if expected_id and post_id and expected_id != post_id:
                return FacebookPermalinkResult(
                    False,
                    job_id,
                    error="Verified permalink conflicts with persisted post ID",
                )
            if job.status is not WorkflowStatus.POST_URL_EXTRACTED:
                self.repository.transition(
                    job_id,
                    WorkflowStatus.POST_URL_EXTRACTED,
                    event_type="VERIFIED_PERMALINK_REUSED",
                    details={"extraction_method": "persisted_verified_permalink"},
                    data_patch={
                        "facebook_post_url": canonical,
                        "facebook_post_id": post_id or expected_id or None,
                        "facebook_permalink_extraction_method": "persisted_verified_permalink",
                        "facebook_permalink_error": None,
                    },
                )
            elif canonical != persisted_permalink:
                self.repository.update_data(
                    job_id, {"facebook_post_url": canonical}
                )
            return FacebookPermalinkResult(
                True,
                job_id,
                canonical,
                post_id or expected_id or None,
                "persisted_verified_permalink",
                [],
                None,
            )
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
            page, "facebook.next_button", timeout_ms=10_000,
            diagnostics_dir=diagnostics,
            context=f"job_id={job_id} state=FACEBOOK_PREPARING action=verify_next_ready",
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

    def _record_publish_milestone(
        self, job_id: str, event: str, *, persist: bool = True, **details: Any
    ) -> None:
        payload = {key: value for key, value in details.items() if value is not None}
        self.logger.info(
            event,
            extra={
                "component": "facebook_publish",
                "event": event,
                "job_id": job_id,
                "attempt": payload.get("attempt"),
                "details": payload,
            },
        )
        if persist:
            self.repository.record_event(job_id, event_type=event, details=payload)

    async def _wait_for_publish_to_settle(
        self, page: Any, *, job_id: str, submitted_at: datetime
    ) -> PublishSubmitResult:
        """Observe bounded post-submit UI signals without ever clicking Publish again."""
        started = time.monotonic()
        deadline = started + self.settings.facebook_publish_settle_timeout_seconds
        posting_seen = False
        posting_cleared = False
        composer_closed = False
        success_notification = False
        interstitial_detected = False
        interstitial_dismissed = False
        composer_closed_logged = False

        while time.monotonic() < deadline:
            dismissed = await self._dismiss_post_publish_interstitials(page, job_id)
            interstitial_detected = (
                interstitial_detected or job_id in self._interstitial_detected_jobs
            )
            interstitial_dismissed = interstitial_dismissed or dismissed

            posting_visible = await self.resolver.exists(
                page, "facebook.posting_indicator", timeout_ms=250
            )
            if posting_visible and not posting_seen:
                posting_seen = True
                self._record_publish_milestone(
                    job_id,
                    "posting_indicator_detected",
                    submitted_at=submitted_at.isoformat(),
                    elapsed_seconds=round(time.monotonic() - started, 3),
                )
            if posting_seen and not posting_visible:
                posting_cleared = True

            success_notification = success_notification or await self.resolver.exists(
                page, "facebook.publish_success", timeout_ms=250
            )
            composer_closed = not await self.resolver.exists(
                page, "facebook.composer_dialog", timeout_ms=250
            )
            if composer_closed and not composer_closed_logged:
                composer_closed_logged = True
                self._record_publish_milestone(
                    job_id,
                    "composer_closed",
                    submitted_at=submitted_at.isoformat(),
                    elapsed_seconds=round(time.monotonic() - started, 3),
                )

            if success_notification or (composer_closed and not posting_visible):
                break
            await asyncio.sleep(self.settings.facebook_publish_poll_interval_seconds)

        return PublishSubmitResult(
            submitted=True,
            posting_indicator_detected=posting_seen,
            posting_indicator_cleared=posting_cleared,
            composer_closed=composer_closed,
            success_notification=success_notification,
            interstitial_detected=interstitial_detected,
            interstitial_dismissed=interstitial_dismissed,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )

    async def _verify_publication(
        self, page: Any, job_id: str, text: str, images: list[Path],
        started: datetime, before_ids: set[str]
    ) -> tuple[FacebookPublishResult, dict[str, Any]]:
        timeout_seconds = (
            self.settings.facebook_post_discovery_timeout_seconds
            if self._reload_feed_during_verification
            else self.settings.facebook_publish_timeout_seconds
        )
        deadline = time.monotonic() + timeout_seconds
        next_reload = (
            time.monotonic()
            + self.settings.facebook_reconciliation_reload_initial_seconds
        )
        reload_delay = self.settings.facebook_reconciliation_reload_initial_seconds
        signals: dict[str, Any] = {}
        signals.update(
            self._require_job(job_id).data.get("facebook_publish_settle_signals") or {}
        )
        candidate: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            await self._dismiss_post_publish_interstitials(page, job_id)
            signals["success_notification"] = await self.resolver.exists(
                page, "facebook.publish_success", timeout_ms=400
            )
            signals["composer_closed"] = not await self.resolver.exists(
                page, "facebook.composer_dialog", timeout_ms=300
            )
            direct_candidates = await self._find_direct_result_permalinks(
                page, before_ids
            )
            if (
                type(self)._find_exact_new_post
                is not FacebookWebClient._find_exact_new_post
            ):
                legacy_candidate = await self._find_exact_new_post(
                    page, text, started, len(images), before_ids, job_id=job_id
                )
                feed_candidates = [legacy_candidate] if legacy_candidate else []
            else:
                feed_candidates = await self._find_exact_new_posts(
                    page, text, started, len(images), before_ids, job_id=job_id
                )
            candidates: list[dict[str, Any]] = []
            seen_urls: set[str] = set()
            for item in [*direct_candidates, *feed_candidates]:
                url = str(item.get("url") or "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    candidates.append(item)
            if len(candidates) > 1:
                matching_permalinks = [str(item["url"]) for item in candidates]
                for item in candidates:
                    self._record_candidate_observation(job_id, item, accepted=False)
                signals["matching_post_count"] = len(candidates)
                return FacebookPublishResult(
                    success=False,
                    status="POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW",
                    target_url=str(
                        self._require_job(job_id).data.get(
                            "facebook_target_url", ""
                        )
                    ),
                    job_id=job_id,
                    diagnostics={
                        "signals": signals,
                        "matching_permalinks": matching_permalinks,
                    },
                    error="Multiple matching Facebook posts require manual review",
                ), signals
            candidate = candidates[0] if candidates else None
            signals["direct_permalink"] = bool(direct_candidates)
            signals["exact_post_match"] = bool(candidate)
            signals["text_match"] = bool(candidate and candidate.get("text_match"))
            signals["recent_timestamp"] = bool(candidate and candidate.get("recent"))
            signals["image_count_match"] = bool(
                candidate and candidate.get("image_count") == len(images)
            )
            if self.publication_is_verified(signals):
                url = candidate.get("url") if candidate else None
                post_id = self.extract_post_id(url or "")
                if candidate:
                    self._record_candidate_observation(job_id, candidate, accepted=True)
                methods = [key for key, value in signals.items() if value]
                target_url = self._require_job(job_id).data.get("facebook_target_url", "")
                return FacebookPublishResult(
                    success=True,
                    status="PUBLISHED_VERIFIED",
                    target_url=target_url,
                    post_id=post_id,
                    permalink=url,
                    published_at=datetime.now(UTC),
                    verification_method="+".join(methods),
                    job_id=job_id,
                    post_url=url,
                    diagnostics={"signals": signals, "candidate": candidate or {}},
                ), signals
            if candidate:
                self._record_candidate_observation(job_id, candidate, accepted=False)
            if (
                self._reload_feed_during_verification
                and time.monotonic() >= next_reload
            ):
                await page.reload(
                    wait_until="domcontentloaded",
                    timeout=self.settings.facebook_navigation_timeout_ms,
                )
                next_reload = time.monotonic() + reload_delay
                reload_delay = min(
                    self.settings.facebook_reconciliation_reload_max_seconds,
                    reload_delay * self.settings.retry_multiplier,
                )
            await asyncio.sleep(self.settings.facebook_publish_poll_interval_seconds)
        target_url = self._require_job(job_id).data.get("facebook_target_url", "")
        return FacebookPublishResult(
            success=False,
            status="PUBLICATION_UNCERTAIN",
            target_url=target_url,
            job_id=job_id,
            diagnostics={"signals": signals},
            warnings=["Publication could not be verified strongly enough"],
            error="Facebook publication outcome is uncertain",
        ), signals

    async def _dismiss_post_publish_interstitials(
        self, page: Any, job_id: str
    ) -> bool:
        """Dismiss only known-safe controls inside a recognized late dialog."""
        selector_pairs = (
            (
                "facebook.post_publish_interstitial_dialog",
                "facebook.post_publish_interstitial_dismiss",
            ),
            ("facebook.post_publish_upsell_dialog", "facebook.post_publish_upsell_dismiss"),
        )
        for dialog_key, dismiss_key in selector_pairs:
            try:
                if not await self.resolver.exists(page, dialog_key, timeout_ms=250):
                    continue
                if job_id not in self._interstitial_detected_jobs:
                    self._interstitial_detected_jobs.add(job_id)
                    self._record_publish_milestone(
                        job_id,
                        "late_interstitial_detected",
                        submitted_at=self._require_job(job_id).data.get(
                            "facebook_submitted_at"
                        ),
                    )
                dialog = await self.resolver.find_first(
                    page, dialog_key, timeout_ms=750
                )
                dismiss = await self.resolver.find_first(
                    dialog, dismiss_key, timeout_ms=750
                )
                await dismiss.click()
                self._interstitial_dismissed_jobs.add(job_id)
                self._record_publish_milestone(
                    job_id,
                    "late_interstitial_dismissed",
                    submitted_at=self._require_job(job_id).data.get(
                        "facebook_submitted_at"
                    ),
                )
                return True
            except (SelectorResolutionError, KeyError):
                if job_id not in self._interstitial_diagnostic_jobs:
                    self._interstitial_diagnostic_jobs.add(job_id)
                    self._record_publish_milestone(
                        job_id,
                        "late_interstitial_dismiss_failed",
                        submitted_at=self._require_job(job_id).data.get(
                            "facebook_submitted_at"
                        ),
                        match_reason="no_known_safe_dismiss_control",
                    )
                    await self._save_diagnostics(
                        page, job_id, "interstitial-no-safe-dismiss"
                    )
                return False
        return False

    async def _dismiss_post_publish_upsell(self, page: Any) -> bool:
        """Compatibility wrapper for older tests and integrations."""
        job_id = next(iter(self._pages), "")
        if not job_id:
            return False
        return await self._dismiss_post_publish_interstitials(page, job_id)

    @staticmethod
    def publication_is_verified(signals: dict[str, Any]) -> bool:
        strong = bool(
            signals.get("success_notification")
            or signals.get("exact_post_match")
            or signals.get("direct_permalink")
        )
        supporting = sum(
            bool(signals.get(key))
            for key in ("composer_closed", "text_match", "recent_timestamp", "image_count_match")
        )
        return strong and supporting >= 1

    async def _find_exact_new_posts(
        self, page: Any, approved_text: str, started: datetime,
        expected_images: int, before_ids: set[str], *, job_id: str | None = None
    ) -> list[dict[str, Any]]:
        articles = await self._all_locators(page, "facebook.feed_post")
        normalized_approved = self._normalize_text(approved_text)
        matches: list[tuple[int, dict[str, Any]]] = []
        for article in articles:
            try:
                body = self._normalize_text(await article.inner_text())
                caption_match = self._caption_match(normalized_approved, body)
                text_match = bool(caption_match["matched"])
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
                    score = (
                        int(caption_match["score"])
                        + 2 * int(is_new)
                        + 2 * int(recent)
                        + int(images == expected_images)
                    )
                    candidate = {
                        "url": url, "post_id": post_id, "text_match": text_match,
                        "recent": recent, "image_count": images,
                        "match_score": score,
                        "match_reason": caption_match["reason"],
                        "method": "normalized-caption+new-id+timestamp+image-count",
                    }
                    if text_match and is_new:
                        matches.append((score, candidate))
                    elif job_id:
                        self._record_candidate_observation(
                            job_id, candidate, accepted=False
                        )
            except Exception:
                continue
        if matches:
            unique: dict[str, tuple[int, dict[str, Any]]] = {}
            for score, candidate in matches:
                url = str(candidate.get("url") or "")
                if url not in unique or score > unique[url][0]:
                    unique[url] = (score, candidate)
            return [
                candidate
                for _score, candidate in sorted(
                    unique.values(), key=lambda item: item[0], reverse=True
                )
            ]
        page_layout = await self._find_page_layout_post(
            page,
            approved_text,
            expected_images,
            before_ids,
        )
        return [page_layout] if page_layout else []

    async def _find_exact_new_post(
        self, page: Any, approved_text: str, started: datetime,
        expected_images: int, before_ids: set[str], *, job_id: str | None = None
    ) -> dict[str, Any] | None:
        matches = await self._find_exact_new_posts(
            page,
            approved_text,
            started,
            expected_images,
            before_ids,
            job_id=job_id,
        )
        return matches[0] if matches else None

    async def _find_direct_result_permalinks(
        self, page: Any, before_ids: set[str]
    ) -> list[dict[str, Any]]:
        """Read a durable permalink exposed by Facebook's post-submit UI."""
        matches: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for locator in await self._all_locators(
            page, "facebook.publish_result_permalink"
        ):
            try:
                href = await locator.get_attribute("href")
                url = self.normalize_permalink(href or "", base_url=str(page.url))
                post_id = self.extract_post_id(url) or ""
                if post_id and post_id in before_ids:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                matches.append({
                    "url": url,
                    "post_id": post_id,
                    "text_match": False,
                    "recent": True,
                    "image_count": None,
                    "match_score": 10,
                    "match_reason": "facebook_publish_result_permalink",
                    "method": "direct-post-submit-permalink",
                })
            except (AttributeError, ValueError):
                continue
        return matches

    async def _find_direct_result_permalink(
        self, page: Any, before_ids: set[str]
    ) -> dict[str, Any] | None:
        matches = await self._find_direct_result_permalinks(page, before_ids)
        return matches[0] if matches else None

    def _record_candidate_observation(
        self, job_id: str, candidate: dict[str, Any], *, accepted: bool
    ) -> None:
        url = str(candidate.get("url") or "")
        event = "candidate_post_found" if accepted else "candidate_post_rejected"
        key = (job_id, event, url)
        if key in self._candidate_observations:
            return
        self._candidate_observations.add(key)
        self._record_publish_milestone(
            job_id,
            event,
            submitted_at=self._require_job(job_id).data.get("facebook_submitted_at"),
            candidate_url=url,
            match_score=candidate.get("match_score"),
            match_reason=candidate.get("match_reason") or candidate.get("method"),
        )

    @classmethod
    def _pcb_post_permalink(cls, href: str, *, base_url: str) -> str | None:
        absolute = urljoin(base_url, str(href or "").strip())
        parsed = urlsplit(absolute)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        match = re.fullmatch(r"pcb\.(\d+)", str(query.get("set") or ""))
        if not match:
            return None
        post_id = match.group(1)

        base = urlsplit(base_url)
        base_query = dict(parse_qsl(base.query, keep_blank_values=True))
        actor_id = str(base_query.get("id") or "").strip()
        if not actor_id:
            segments = [segment for segment in base.path.split("/") if segment]
            if segments and segments[0] not in {"profile.php", "me"}:
                actor_id = segments[0]
        if not actor_id:
            return None
        return f"https://www.facebook.com/{actor_id}/posts/{post_id}"

    @staticmethod
    def _page_layout_match_tokens(approved_text: str) -> list[str]:
        tokens: list[str] = []
        for value in re.findall(r"https?://[^\s]+", approved_text):
            cleaned = value.rstrip(".,);]")
            parsed = urlsplit(cleaned)
            reel_match = re.search(r"/reel/(\d+)", parsed.path)
            if reel_match:
                tokens.append(reel_match.group(1).casefold())
                continue
            view_id = dict(parse_qsl(parsed.query, keep_blank_values=True)).get("view")
            if view_id:
                tokens.append(str(view_id).casefold())
                continue
            tokens.append(cleaned.casefold())
        return tokens

    async def _find_page_layout_post(
        self,
        page: Any,
        approved_text: str,
        expected_images: int,
        before_ids: set[str],
    ) -> dict[str, Any] | None:
        """Find Page-owner timeline posts that omit ``role=article``.

        Facebook exposes the durable post ID in image links as
        ``set=pcb.<post-id>``. The surrounding post is matched using the source
        and analysis URLs from the approved text, after expanding collapsed
        post bodies.
        """
        links = page.locator('a[href*="set=pcb."]')
        await self._expand_collapsed_posts(page)

        match_tokens = self._page_layout_match_tokens(approved_text)
        if not match_tokens:
            return None

        links = page.locator('a[href*="set=pcb."]')
        seen_post_ids: set[str] = set()
        for index in range(await links.count()):
            link = links.nth(index)
            try:
                href = await link.get_attribute("href")
                permalink = self._pcb_post_permalink(
                    str(href or ""),
                    base_url=str(page.url),
                )
                if not permalink:
                    continue
                post_id = self.extract_post_id(permalink) or ""
                if (
                    not post_id
                    or post_id in seen_post_ids
                    or post_id in before_ids
                ):
                    continue
                seen_post_ids.add(post_id)
                match = await link.evaluate(
                    """(node, args) => {
                        let current = node;
                        for (let depth = 0; current && depth < 18; depth += 1) {
                            const text = (current.innerText || "")
                                .replace(/\\s+/g, " ")
                                .trim()
                                .toLowerCase();
                            if (args.tokens.every(token => text.includes(token))) {
                                const photoIds = new Set();
                                for (const anchor of current.querySelectorAll('a[href*="set=pcb."]')) {
                                    try {
                                        const url = new URL(anchor.href);
                                        if (url.searchParams.get("set") === `pcb.${args.postId}`) {
                                            const photoId = url.searchParams.get("fbid");
                                            if (photoId) photoIds.add(photoId);
                                        }
                                    } catch (_) {}
                                }
                                return {imageCount: photoIds.size};
                            }
                            current = current.parentElement;
                        }
                        return null;
                    }""",
                    {"tokens": match_tokens, "postId": post_id},
                )
                if not match:
                    continue
                image_count = int(match.get("imageCount") or 0)
                return {
                    "url": permalink,
                    "post_id": post_id,
                    "text_match": True,
                    "recent": False,
                    "image_count": image_count,
                    "method": "content-urls+pcb-post-id+image-count",
                }
            except Exception:
                continue
        try:
            await page.mouse.wheel(0, 700)
            await page.wait_for_timeout(1_000)
        except Exception:
            pass
        return None

    @staticmethod
    async def _expand_collapsed_posts(page: Any) -> None:
        """Expand Page timeline text across Facebook's button/span variants."""
        for label in ("Xem thêm", "See more"):
            try:
                buttons = page.get_by_role("button", name=label, exact=True)
                for index in reversed(range(min(await buttons.count(), 50))):
                    button = buttons.nth(index)
                    if await button.is_visible():
                        await button.click()
            except Exception:
                pass

            # Page-owner timelines currently expose the visible label as a
            # plain SPAN inside a clickable ancestor, not as an ARIA button.
            try:
                labels = page.get_by_text(label, exact=True)
                for index in reversed(range(min(await labels.count(), 50))):
                    item = labels.nth(index)
                    if await item.is_visible():
                        await item.evaluate(
                            """node => {
                                const target = node.closest('[role="button"], button, a') || node;
                                target.click();
                            }"""
                        )
            except Exception:
                pass

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
        attempt_json = (folder / "attempt.json").resolve()
        
        try:
            await page.screenshot(path=str(screenshot), full_page=True)
        except Exception:
            pass
            
        try:
            title = await page.title()
        except Exception:
            title = "Unknown"
            
        job = self._require_job(job_id)
        
        info = {
            "url": safe_browser_url(str(getattr(page, "url", ""))),
            "name": name,
            "title": title,
            "job_id": job_id,
            "target_url": job.data.get("facebook_target_url"),
            "content_fingerprint": job.data.get("facebook_content_hash"),
        }
        metadata.write_text(json.dumps(info, indent=2), encoding="utf-8")
        attempt_json.write_text(json.dumps(info, indent=2), encoding="utf-8")
        
        artifact = metadata
        if self.settings.save_diagnostic_html:
            artifact = (folder / f"{name}.html").resolve()
            try:
                artifact.write_text(await page.content(), encoding="utf-8")
            except Exception:
                pass
        
        for path in (screenshot, metadata, attempt_json, artifact):
            if path.exists():
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
        job = self._require_job(job_id)
        raw_started = job.data.get("facebook_publication_started_at")
        ts = "latest"
        if raw_started:
            try:
                dt = datetime.fromisoformat(raw_started.replace("Z", "+00:00"))
                ts = dt.strftime("%Y%m%dT%H%M%SZ")
            except Exception:
                pass
        return self.settings.project_root / "runtime" / "diagnostics" / "jobs" / job_id / ts / "facebook-publish"

    @staticmethod
    async def _input_text(locator: Any) -> str:
        try:
            return await locator.input_value()
        except Exception:
            return (await locator.text_content() or "").strip()

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        cleaned = re.sub(r'[^\w\s]', '', normalized)
        return " ".join(cleaned.split()).casefold()

    @classmethod
    def _caption_match(cls, approved: str, rendered: str) -> dict[str, Any]:
        """Score normalized full/prefix/suffix caption evidence."""
        expected = cls._normalize_text(approved)
        actual = cls._normalize_text(rendered)
        if not expected or not actual:
            return {"matched": False, "score": 0, "reason": "empty_caption"}

        chunk = min(100, max(30, len(expected) // 3))
        prefix = expected[:chunk].strip()
        suffix = expected[-chunk:].strip()
        reasons: list[str] = []
        score = 0
        if expected in actual:
            reasons.append("full")
            score += 8
        if prefix and prefix in actual:
            reasons.append("prefix")
            score += 4
        if suffix and suffix in actual:
            reasons.append("suffix")
            score += 3
        matched = "full" in reasons or "prefix" in reasons or (
            "suffix" in reasons and len(expected) <= 120
        )
        return {
            "matched": matched,
            "score": score,
            "reason": "+".join(reasons) if reasons else "caption_mismatch",
        }

    @staticmethod
    def _publication_was_submitted(data: dict[str, Any]) -> bool:
        return facebook_submission_is_committed(data)

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
