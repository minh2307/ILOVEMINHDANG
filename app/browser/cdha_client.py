from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.browser.chrome_manager import ChromeManager
from app.browser.cdha_state import (
    AuthenticationState,
    CDHAState,
    CDHAStateSnapshot,
    CDHAStateTimeoutError,
    wait_for_cdha_state,
)
from app.browser.selector_resolver import SelectorResolutionError, SelectorResolver
from app.config.settings import Settings
from app.domain.models.cdha_clinical_summary import CDHAClinicalSummary
from app.models.results import CDHAAnalysisResult
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.privacy_service import PrivacyService
from app.services.screenshot_service import ScreenshotService
from app.browser.error_mapper import map_playwright_error
from app.error_events import build_error_event_details
from app.errors import (
    AuthenticationRequiredError,
    CDHAAuthenticationRequiredError,
    CDHAControlDisabledError,
    CDHAControlHiddenError,
    CDHARenderError,
    CDHASelectorMismatchError,
    BrowserContextClosedError,
    BrowserDisconnectedError,
    BrowserPageClosedError,
    CDHARenderError,
    CDHAUploadError,
    FrameNotReadyError,
    PipelineError,
    SelectorNotFoundError,
)
from app.services.retry_service import RetryAttempt, RetryPolicy, retry_async
from app.domain.policies.external_side_effect_policy import (
    CDHACheckpoint,
    LargeUploadApproval,
    sha256_file,
)


SUPPORTED_VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov"})


class CDHAWebClient:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        chrome: ChromeManager,
        *,
        resolver: SelectorResolver | None = None,
        screenshots: ScreenshotService | None = None,
        privacy: PrivacyService | None = None,
        logger: logging.Logger | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.chrome = chrome
        self.resolver = resolver or SelectorResolver(settings.selectors_path, save_html=settings.save_diagnostic_html)
        self.screenshots = screenshots or ScreenshotService(self.resolver)
        self.privacy = privacy or PrivacyService()
        self.logger = logger or logging.getLogger("cdha_pipeline.cdha")

    async def analyze_video(
        self, *, video_path: Path, clinical_factors: str, job_id: str
    ) -> CDHAAnalysisResult:
        job = self.repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        if job.status is WorkflowStatus.CDHA_FAILED:
            job = self.repository.transition(
                job_id, WorkflowStatus.RETRY_PENDING, details={"retry_step": "cdha"}
            )
        if job.status not in {
            WorkflowStatus.CLINICAL_FACTORS_GENERATED,
            WorkflowStatus.RETRY_PENDING,
            WorkflowStatus.NEEDS_CDHA_LOGIN,
        }:
            raise ValueError(
                "CDHA analysis requires CLINICAL_FACTORS_GENERATED or a retryable CDHA job; "
                f"got {job.status.value}"
            )
        factors = self.privacy.mask(str(clinical_factors or "").strip())
        if not factors:
            raise ValueError("Validated Clinical Factors are required")
        if self.privacy.contains_obvious_identifier(factors):
            raise ValueError("Masked Clinical Factors still contain an obvious identifier")

        started_at = datetime.now(UTC).isoformat()
        if job.status is not WorkflowStatus.NEEDS_CDHA_LOGIN:
            self.repository.transition(
                job_id,
                WorkflowStatus.CDHA_OPENING,
                details={"url": self.settings.cdha_url},
                data_patch={"cdha_started_at": started_at, "cdha_error": None},
            )
        else:
            self.repository.update_data(
                job_id, {"cdha_error": None, "cdha_resumed_at": started_at}
            )
        job_dir = (self.settings.job_data_dir / job_id).resolve()
        diagnostics_dir = job_dir / "diagnostics"
        job_dir.mkdir(parents=True, exist_ok=True)
        masked_factors_path = (job_dir / "clinical-factors-masked.txt").resolve()
        self._write_text_atomic(masked_factors_path, factors)
        page: Any = None
        try:
            acquire_page = getattr(self.chrome, "acquire_page", None)
            page = (
                await acquire_page(f"cdha:{job_id}")
                if acquire_page is not None
                else await self.chrome.new_page()
            )
            await page.goto(
                self.settings.cdha_url,
                wait_until="domcontentloaded",
                timeout=self.settings.browser_navigation_timeout_seconds * 1000,
            )
            if not await self.is_authenticated(page):
                current = self.repository.get_job(job_id)
                if current and current.status is not WorkflowStatus.NEEDS_CDHA_LOGIN:
                    self.repository.transition(
                        job_id,
                        WorkflowStatus.NEEDS_CDHA_LOGIN,
                        details={"reason": "login_or_manual_security_action_required"},
                    )
                raise CDHAAuthenticationRequiredError(
                    "CDHA authenticated page was not verified",
                    phase="CDHA_OPENING", operation="authenticate", job_id=job_id,
                )
            if "modality=us_video" not in str(page.url) and not await self.resolver.exists(
                page, "cdha.modality_marker", timeout_ms=2_000
            ):
                raise RuntimeError("CDHA ultrasound-video modality could not be verified")

            job = self.repository.get_job(job_id) or job
            fingerprint = self.submission_fingerprint(job)
            self.repository.update_data(
                job_id, {"cdha_submission_fingerprint": fingerprint}
            )
            view_url = self.existing_analysis_url(job)
            existing_result_resume = bool(
                view_url
                and (
                    job.data.get("cdha_result_json_path")
                    or job.data.get("cdha_result")
                    or job.data.get("cdha_external_analysis_id")
                )
            )
            if not view_url:
                checkpoint = CDHACheckpoint.from_data(job.data)
                if checkpoint.reconciliation_only:
                    raise CDHAUploadError(
                        "A prior CDHA submission may already exist; reconcile it "
                        "before any resubmission",
                        error_code="CDHA_SUBMISSION_UNCERTAIN",
                        retryable=False,
                        manual_action_required=True,
                        phase="CDHA_UPLOADING",
                        operation="reconcile_submission",
                        job_id=job_id,
                        details={
                            "current_cdha_state": checkpoint.value,
                            "submission_fingerprint": fingerprint,
                        },
                    )
                video = self.validate_video_path(video_path)
                self.repository.transition(
                    job_id,
                    WorkflowStatus.CDHA_UPLOADING,
                    details={"video_path": str(video)},
                )
                self.logger.info("Uploading local video file via CDHA iframe", extra={"job_id": job_id})
                
                if checkpoint is CDHACheckpoint.UPLOAD_NOT_STARTED:
                    await self._prepare_video_upload(
                        page,
                        video,
                        job_id=job_id,
                        diagnostics_dir=diagnostics_dir,
                        submission_fingerprint=fingerprint,
                    )
                elif checkpoint is CDHACheckpoint.UPLOAD_CONFIRMED:
                    await self._wait_for_stable_upload_completion(
                        page, video, job_id=job_id
                    )

                factors_input = await self.resolver.find_first(
                    page,
                    "cdha.clinical_factors",
                    timeout_ms=10_000,
                    diagnostics_dir=diagnostics_dir,
                    context=f"job_id={job_id} state=CDHA_UPLOADING action=insert_factors",
                )
                await factors_input.fill(factors)
                if await self._input_text(factors_input) != factors:
                    raise RuntimeError("CDHA Clinical Factors text verification failed")
                await self._request_analysis_once(
                    page, job_id=job_id, diagnostics_dir=diagnostics_dir
                )
                self.repository.transition(
                    job_id,
                    WorkflowStatus.CDHA_ANALYZING,
                    details={"action": "analysis_started"},
                )
                await self._wait_for_analysis(page, job_id=job_id)
                analysis_url = str(getattr(page, "url", "") or "")
                external_id = dict(
                    parse_qsl(
                        urlsplit(analysis_url).query, keep_blank_values=True
                    )
                ).get("view")
                if external_id:
                    self.repository.update_data(
                        job_id,
                        {
                            "cdha_submission_state": "SUBMITTED",
                            "cdha_checkpoint": CDHACheckpoint.ANALYSIS_CONFIRMED.value,
                            "cdha_external_analysis_id": external_id,
                            "cdha_view_url": analysis_url,
                            "cdha_submitted_at": datetime.now(UTC).isoformat(),
                        },
                    )
                extracted = await self.extract_result(page, job_id, started_at)
            else:
                self.logger.info(f"CDHA analysis already completed, jumping to result URL: {view_url}", extra={"job_id": job_id})
                # Satisfy state machine
                self.repository.transition(job_id, WorkflowStatus.CDHA_UPLOADING, details={"skipped": True})
                self.repository.transition(job_id, WorkflowStatus.CDHA_ANALYZING, details={"skipped": True})
                
                await page.goto(view_url)
                extracted = await self.extract_result(page, job_id, started_at)
            summary = CDHAClinicalSummary.from_values(
                key_findings=extracted.key_findings,
                impression=extracted.impression,
                analysis_url=extracted.analysis_url or str(page.url),
                source_language=extracted.source_language,
                raw_key_findings=extracted.raw_key_findings,
                raw_impression=extracted.raw_impression,
            )
            original_view_url = summary.analysis_url
            raw_text_path = (job_dir / "cdha-result-raw.txt").resolve()
            html_path: Path | None = None
            json_path = (job_dir / "cdha-result.json").resolve()
            diagnostic_path = (diagnostics_dir / "cdha-result-full-page.png").resolve()
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
            self._write_text_atomic(raw_text_path, extracted.raw_text)
            if self.settings.save_diagnostic_html:
                html_path = (job_dir / "cdha-result.html").resolve()
                self._write_text_atomic(html_path, await page.content())
            await page.screenshot(path=str(diagnostic_path), full_page=True)
            completed_at = datetime.now(UTC).isoformat()
            extracted = CDHAAnalysisResult(
                success=True,
                job_id=job_id,
                triage=extracted.triage,
                confidence=extracted.confidence,
                key_findings=extracted.key_findings,
                impression=extracted.impression,
                analysis_url=original_view_url,
                source_language=summary.source_language,
                raw_key_findings=summary.raw_key_findings,
                raw_impression=summary.raw_impression,
                detailed_analysis=extracted.detailed_analysis,
                marked_regions=extracted.marked_regions,
                raw_text=extracted.raw_text,
                result_json_path=json_path,
                result_html_path=html_path,
                diagnostic_screenshot_path=diagnostic_path,
                started_at=started_at,
                completed_at=completed_at,
                warnings=extracted.warnings,
            )
            self._write_json_atomic(json_path, extracted.to_dict())
            self.repository.transition(
                job_id,
                WorkflowStatus.CDHA_ANALYZED,
                details={"result_json_path": str(json_path)},
                data_patch={
                    "cdha_result": extracted.to_dict(),
                    "cdha_result_json_path": str(json_path),
                    "cdha_result_raw_path": str(raw_text_path),
                    "cdha_result_html_path": str(html_path) if html_path else None,
                    "cdha_diagnostic_screenshot_path": str(diagnostic_path),
                    "cdha_view_url": original_view_url,
                    "cdha_checkpoint": CDHACheckpoint.ANALYSIS_CONFIRMED.value,
                    "clinical_factors_path": str(masked_factors_path),
                    "clinical_factors": factors,
                    "cdha_completed_at": completed_at,
                },
            )

            # Share is a secondary action. The irreversible analysis checkpoint
            # above is persisted first, and this action is never retried by the
            # screenshot recovery loop.
            try:
                current_job = self.repository.get_job(job_id)
                consultation_state = str(
                    (current_job.data if current_job else {}).get(
                        "cdha_consultation_state"
                    )
                    or ""
                ).upper()
                if existing_result_resume or consultation_state in {
                    "REQUESTED",
                    "COMPLETED",
                }:
                    self.logger.info(
                        "Skipping previously attempted Share -> Consultation",
                        extra={"job_id": job_id},
                    )
                else:
                    page = await self._share_consultation_once(
                        page, result_url=original_view_url, job_id=job_id
                    )
            except Exception as exc:
                self.logger.warning(
                    "Could not complete Share -> Consultation",
                    extra={"job_id": job_id, "error_type": type(exc).__name__},
                )

            self.repository.transition(
                job_id,
                WorkflowStatus.SCREENSHOTS_CAPTURING,
                details={"action": "capture"},
            )
            screenshot_paths, screenshot_warnings = await self._capture_result_screenshots(
                page, job_dir, result_url=original_view_url
            )
            all_warnings = [*extracted.warnings, *screenshot_warnings]
            final_result = CDHAAnalysisResult(
                success=True,
                job_id=job_id,
                triage=extracted.triage,
                confidence=extracted.confidence,
                key_findings=extracted.key_findings,
                impression=extracted.impression,
                analysis_url=original_view_url,
                source_language=extracted.source_language,
                raw_key_findings=extracted.raw_key_findings,
                raw_impression=extracted.raw_impression,
                detailed_analysis=extracted.detailed_analysis,
                marked_regions=extracted.marked_regions,
                raw_text=extracted.raw_text,
                result_json_path=json_path,
                result_html_path=html_path,
                diagnostic_screenshot_path=diagnostic_path,
                screenshot_paths=screenshot_paths,
                started_at=started_at,
                completed_at=completed_at,
                warnings=all_warnings,
            )
            self._write_json_atomic(json_path, final_result.to_dict())
            self.repository.transition(
                job_id,
                WorkflowStatus.SCREENSHOTS_CAPTURED,
                details={"count": len(screenshot_paths)},
                data_patch={
                    "screenshot_paths": [str(path) for path in screenshot_paths],
                    "cdha_warnings": all_warnings,
                    "cdha_result": final_result.to_dict(),
                },
            )
            self.repository.transition(
                job_id,
                WorkflowStatus.WAITING_FOR_REVIEW,
                details={"action": "operator_review_required"},
            )
            return final_result
        except Exception as exc:
            current = self.repository.get_job(job_id)
            phase = current.status.value if current else "CDHA"
            mapped = exc if isinstance(exc, PipelineError) else map_playwright_error(
                exc, phase=phase, operation="analyze_video", job_id=job_id
            )
            failure_diagnostics: tuple[Path, ...] | None = None
            if page is not None:
                try:
                    diagnostic_stamp = datetime.now(UTC).strftime(
                        "%Y%m%dT%H%M%S.%fZ"
                    )
                    failure_dir = (
                        diagnostics_dir / diagnostic_stamp / phase.casefold()
                    )
                    failure_diagnostics = await self.chrome.save_diagnostics(
                        page,
                        failure_dir,
                        "cdha-failure",
                        details={
                            "job_id": job_id,
                            "workflow_stage": phase,
                            "current_cdha_state": mapped.details.get(
                                "current_cdha_state", "UNKNOWN"
                            ),
                            "selector_attempts": mapped.details.get(
                                "selector_attempts", []
                            ),
                            "timeout_seconds": mapped.details.get(
                                "timeout_seconds"
                            ),
                            "attempt": getattr(current, "attempt_count", None),
                            "queue_lease": {
                                "claimed_by": getattr(current, "claimed_by", None),
                                "lease_expires_at": getattr(
                                    current, "lease_expires_at", None
                                ),
                                "last_heartbeat": getattr(
                                    current, "last_heartbeat", None
                                ),
                            },
                            "persisted_job_state": phase,
                            "error_code": mapped.error_code,
                        },
                    )
                except Exception as diagnostic_error:
                    self.logger.warning(
                        "Failed to save CDHA diagnostics",
                        extra={"job_id": job_id, "error_type": type(diagnostic_error).__name__},
                    )
            if failure_diagnostics:
                mapped.diagnostic_paths = tuple(str(path) for path in failure_diagnostics)
            details = build_error_event_details(
                mapped, browser_url=str(getattr(page, "url", ""))
            )
            error = str(details.get("message") or mapped.error_code)
            if current and current.status in {
                WorkflowStatus.CDHA_OPENING,
                WorkflowStatus.CDHA_UPLOADING,
                WorkflowStatus.CDHA_ANALYZING,
                WorkflowStatus.SCREENSHOTS_CAPTURING,
            }:
                completed_at = datetime.now(UTC).isoformat()
                failure_patch = {
                    "cdha_error": error,
                    "cdha_error_code": mapped.error_code,
                    "cdha_error_retryable": mapped.retryable,
                    "cdha_manual_action_required": mapped.manual_action_required,
                    "cdha_completed_at": completed_at,
                }
                if failure_diagnostics:
                    failure_patch["cdha_failure_diagnostic_paths"] = [
                        str(path) for path in failure_diagnostics
                    ]
                    screenshot = next(
                        (
                            path
                            for path in failure_diagnostics
                            if path.suffix.casefold() == ".png"
                        ),
                        None,
                    )
                    html = next(
                        (
                            path
                            for path in failure_diagnostics
                            if path.suffix.casefold() == ".html"
                        ),
                        None,
                    )
                    if screenshot:
                        failure_patch["cdha_failure_screenshot_path"] = str(
                            screenshot
                        )
                    if html:
                        failure_patch["cdha_failure_html_path"] = str(html)
                failure_status = (
                    WorkflowStatus.SCREENSHOTS_FAILED
                    if current.status is WorkflowStatus.SCREENSHOTS_CAPTURING
                    else WorkflowStatus.CDHA_FAILED
                )
                self.repository.transition(
                    job_id,
                    failure_status,
                    details=details,
                    data_patch=failure_patch,
                )
            completed_at = datetime.now(UTC).isoformat()
            self.logger.error(
                "CDHA step failed",
                extra={"job_id": job_id, "error_code": mapped.error_code},
            )
            return CDHAAnalysisResult(
                success=False, job_id=job_id, started_at=started_at,
                completed_at=completed_at, error=error,
            )
        finally:
            if page is not None:
                release_page = getattr(self.chrome, "release_page", None)
                if release_page is not None:
                    try:
                        await release_page(page)
                    except Exception as release_error:
                        self.logger.warning(
                            "Failed to release adapter-owned CDHA page",
                            extra={
                                "job_id": job_id,
                                "error_type": type(release_error).__name__,
                            },
                        )

    async def _share_consultation_once(
        self, page: Any, *, result_url: str, job_id: str
    ) -> Any:
        share = await self.resolver.find_first(
            page, "cdha.share_button", timeout_ms=3_000
        )
        await share.click(
            timeout=int(self.settings.browser_action_timeout_seconds * 1000)
        )
        # Resolve Consultation only after Share; Share can replace the menu DOM.
        consultation = await self.resolver.find_first(
            page, "cdha.consultation", timeout_ms=3_000
        )
        self.repository.update_data(
            job_id,
            {
                "cdha_consultation_state": "REQUESTED",
                "cdha_consultation_requested_at": datetime.now(UTC).isoformat(),
            },
        )
        await consultation.click(
            timeout=int(self.settings.browser_action_timeout_seconds * 1000)
        )
        try:
            await page.wait_for_load_state(
                "domcontentloaded",
                timeout=int(self.settings.browser_navigation_timeout_seconds * 1000),
            )
        except Exception:
            # An in-page route may not emit a new load event. The URL/result UI
            # check below is the business readiness signal.
            pass
        if result_url and str(getattr(page, "url", "")) != result_url:
            await page.goto(
                result_url,
                wait_until="domcontentloaded",
                timeout=int(self.settings.browser_navigation_timeout_seconds * 1000),
            )
        await self.resolver.find_first(
            page,
            "cdha.result_container",
            timeout_ms=int(self.settings.browser_action_timeout_seconds * 1000),
        )
        self.repository.update_data(
            job_id,
            {
                "cdha_consultation_state": "COMPLETED",
                "cdha_consultation_completed_at": datetime.now(UTC).isoformat(),
            },
        )
        self.logger.info("Completed Share -> Consultation", extra={"result_url": result_url})
        return page

    async def _capture_result_screenshots(
        self, page: Any, job_dir: Path, *, result_url: str
    ) -> tuple[list[Path], list[str]]:
        deadline = time.monotonic() + self.settings.browser_navigation_timeout_seconds
        attempt = 0
        while True:
            attempt += 1
            try:
                await page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=int(self.settings.browser_navigation_timeout_seconds * 1000),
                )
                return await self.screenshots.capture_required(page, job_dir)
            except Exception:
                if attempt >= 2 or time.monotonic() >= deadline:
                    raise
                # Reacquire only the result page and screenshot locators. Never
                # repeat Share, upload, or Analyze from this recovery path.
                await page.goto(
                    result_url,
                    wait_until="domcontentloaded",
                    timeout=max(1, int((deadline - time.monotonic()) * 1000)),
                )

    async def is_authenticated(self, page: Any) -> bool:
        return (
            await self.detect_authentication_state(page)
            is AuthenticationState.AUTHENTICATED
        )

    async def detect_authentication_state(
        self, page: Any
    ) -> AuthenticationState:
        url = str(getattr(page, "url", "")).casefold()
        if await self.resolver.exists(
            page, "cdha.permission_denied", timeout_ms=500
        ):
            return AuthenticationState.PERMISSION_DENIED
        if await self.resolver.exists(
            page, "cdha.two_factor_markers", timeout_ms=500
        ):
            return AuthenticationState.TWO_FACTOR_REQUIRED
        if await self.resolver.exists(
            page, "cdha.checkpoint_markers", timeout_ms=500
        ):
            return AuthenticationState.CHECKPOINT_REQUIRED
        if await self.resolver.exists(
            page, "cdha.session_expired", timeout_ms=500
        ):
            return AuthenticationState.SESSION_EXPIRED
        if any(marker in url for marker in ("/login", "/signin", "auth.")):
            return AuthenticationState.LOGIN_REQUIRED
        if await self.resolver.exists(page, "cdha.login_markers", timeout_ms=800):
            return AuthenticationState.LOGIN_REQUIRED
        if await self.resolver.exists(page, "cdha.security_markers", timeout_ms=800):
            return AuthenticationState.CHECKPOINT_REQUIRED
        if await self.resolver.exists(
            page, "cdha.authenticated_marker", timeout_ms=1_500
        ):
            return AuthenticationState.AUTHENTICATED
        return AuthenticationState.UNKNOWN

    @staticmethod
    def validate_video_path(video_path: Path) -> Path:
        video = Path(video_path).expanduser().resolve()
        if not video.exists() or not video.is_file():
            raise ValueError(f"Video file does not exist: {video}")
        if video.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise ValueError(f"Unsupported video extension: {video.suffix or '<none>'}")
        if video.stat().st_size <= 0:
            raise ValueError(f"Video file is empty: {video}")
        return video

    @staticmethod
    def submission_fingerprint(job: Any) -> str:
        data = getattr(job, "data", {}) or {}
        source = (
            str(getattr(job, "normalized_source_url", "") or "").strip()
            or str(getattr(job, "source_url", "") or "").strip()
        )
        payload = "\n".join(
            (
                str(getattr(job, "job_id", "")),
                str(data.get("checksum_sha256") or ""),
                source,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def existing_analysis_url(self, job: Any) -> str:
        data = getattr(job, "data", {}) or {}
        persisted = str(data.get("cdha_view_url") or "").strip()
        if persisted:
            return persisted
        external_id = str(data.get("cdha_external_analysis_id") or "").strip()
        if not external_id:
            return ""
        parsed = urlsplit(self.settings.cdha_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["view"] = external_id
        query.pop("modality", None)
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")
        )


    def _cdha_retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=max(1, self.settings.max_cdha_retries),
            initial_delay_seconds=self.settings.retry_initial_delay_seconds,
            multiplier=self.settings.retry_multiplier,
            max_delay_seconds=self.settings.retry_max_delay_seconds,
            jitter_seconds=self.settings.retry_jitter_seconds,
        )

    def _css_candidates(self, key: str) -> tuple[str, ...]:
        selectors: list[str] = []
        for candidate in self.resolver.candidates(key):
            if isinstance(candidate, str):
                selectors.append(candidate)
            elif isinstance(candidate, dict) and isinstance(candidate.get("css"), str):
                selectors.append(candidate["css"])
        if not selectors:
            raise SelectorNotFoundError(
                f"{key} has no CSS selector usable for an iframe",
                phase="CDHA_UPLOADING",
                operation="resolve_upload_frame",
            )
        return tuple(selectors)

    async def _resolve_upload_frame(
        self, page: Any, *, timeout_ms: int = 2_000, visible: bool = True
    ) -> Any:
        failures: list[str] = []
        selectors = self._css_candidates("cdha.upload_frame")
        per_selector_timeout = max(250, timeout_ms // len(selectors))
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                await locator.wait_for(
                    state="visible" if visible else "attached",
                    timeout=per_selector_timeout,
                )
                return page.frame_locator(selector)
            except Exception as exc:
                mapped = map_playwright_error(
                    exc,
                    phase="CDHA_UPLOADING",
                    operation="resolve_upload_frame",
                )
                if mapped.error_code in {
                    "BROWSER_TARGET_CLOSED",
                    "BROWSER_NETWORK_ERROR",
                    "SELECTOR_SYNTAX_ERROR",
                }:
                    raise mapped from exc
                failures.append(f"{selector}:{type(exc).__name__}")
        raise FrameNotReadyError(
            "CDHA upload iframe was not attached before timeout",
            phase="CDHA_UPLOADING",
            operation="resolve_upload_frame",
            details={"selector_attempts": failures, "iframe_count": 0},
        )

    async def _resolve_upload_file_input(
        self, page: Any, *, timeout_ms: int = 2_000
    ) -> Any:
        frame = await self._resolve_upload_frame(
            page, timeout_ms=timeout_ms, visible=True
        )
        try:
            return await self.resolver.find_first(
                frame,
                "cdha.upload_file_input",
                timeout_ms=timeout_ms,
                visible=False,
            )
        except SelectorNotFoundError as exc:
            raise FrameNotReadyError(
                "CDHA upload file input is not attached",
                phase="CDHA_UPLOADING",
                operation="resolve_upload_file_input",
                diagnostic_paths=exc.diagnostic_paths,
            ) from exc

    async def _record_cdha_retry(self, job_id: str, retry: RetryAttempt) -> None:
        error = retry.error
        mapped = error if isinstance(error, PipelineError) else map_playwright_error(
            error,
            phase="CDHA_UPLOADING",
            operation="resolve_upload_file_input",
            job_id=job_id,
        )
        self.repository.record_error(job_id, mapped, attempt=retry.attempt)
        self.logger.warning(
            "Retrying safe CDHA browser operation",
            extra={
                "job_id": job_id,
                "phase": mapped.phase,
                "operation": mapped.operation,
                "attempt": retry.attempt,
                "max_attempts": retry.max_attempts,
                "delay_seconds": retry.delay_seconds,
                "error_code": mapped.error_code,
            },
        )

    async def _ensure_upload_dialog_open(
        self, page: Any, *, job_id: str, diagnostics_dir: Path
    ) -> Any:
        try:
            return await self._resolve_upload_file_input(page, timeout_ms=800)
        except (FrameNotReadyError, SelectorNotFoundError):
            upload_zone = await self.resolver.find_first(
                page,
                "cdha.upload_zone",
                timeout_ms=5_000,
                diagnostics_dir=diagnostics_dir,
                context=f"job_id={job_id} state=CDHA_UPLOADING action=open_upload_dialog",
            )
            await upload_zone.click()

        return await retry_async(
            lambda: self._resolve_upload_file_input(page, timeout_ms=2_000),
            policy=self._cdha_retry_policy(),
            should_retry=lambda exc: bool(
                getattr(exc, "retryable", False)
                and not getattr(exc, "manual_action_required", False)
            ),
            on_retry=lambda retry: self._record_cdha_retry(job_id, retry),
        )

    async def _view_url_value(self, page: Any) -> str:
        try:
            locator = await self.resolver.find_first(
                page, "cdha.view_url_input", timeout_ms=500, visible=False
            )
            return str(await locator.input_value()).strip()
        except (SelectorNotFoundError, KeyError, AttributeError):
            return ""

    async def _reconcile_existing_upload(self, page: Any, video: Path) -> str:
        if await self._view_url_value(page):
            return "complete"
        if await self.resolver.exists(page, "cdha.result_container", timeout_ms=400):
            result_text = await self._optional_text(page, "cdha.result_container")
            if result_text:
                return "complete"
        try:
            frame = await self._resolve_upload_frame(page, timeout_ms=1000, visible=False)
        except Exception:
            frame = page
        if await self.resolver.exists(frame, "cdha.upload_complete", timeout_ms=400):
            return "complete"
        if await self.resolver.exists(frame, "cdha.upload_started", timeout_ms=400):
            return "in_progress"
        filename = await self._optional_text(frame, "cdha.upload_filename")
        if filename and video.name.casefold() in filename.casefold():
            return "uncertain"
        return "not_started"

    async def _wait_for_upload_acknowledgement(self, page: Any) -> None:
        deadline = time.monotonic() + self.settings.page_timeout_seconds
        try:
            frame = await self._resolve_upload_frame(page, timeout_ms=1000, visible=False)
        except Exception:
            frame = page
        while time.monotonic() < deadline:
            if await self.resolver.exists(frame, "cdha.upload_error", timeout_ms=400):
                message = await self._optional_text(frame, "cdha.upload_error")
                raise CDHAUploadError(
                    f"CDHA upload failed: {message or 'file rejected'}",
                    phase="CDHA_UPLOADING",
                    operation="wait_for_upload_acknowledgement",
                    retryable=False,
                )
            if await self._view_url_value(page):
                return
            if await self.resolver.exists(frame, "cdha.upload_started", timeout_ms=400):
                return
            if await self.resolver.exists(frame, "cdha.upload_complete", timeout_ms=400):
                return
            await asyncio.sleep(min(0.5, self.settings.cdha_poll_interval_seconds))
        raise CDHAUploadError(
            "CDHA upload outcome is uncertain because no acknowledgement was observed",
            error_code="CDHA_UPLOAD_UNCERTAIN",
            retryable=False,
            manual_action_required=True,
            phase="CDHA_UPLOADING",
            operation="wait_for_upload_acknowledgement",
        )

    def _validate_video_metadata(
        self, job_id: str, video: Path
    ) -> tuple[int, str]:
        job = self.repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        resolved = self.validate_video_path(video)
        if not resolved.is_file():
            raise CDHAUploadError(
                "CDHA upload path is not a regular file",
                retryable=False,
                manual_action_required=True,
                phase="CDHA_UPLOADING",
                operation="validate_video_metadata",
                job_id=job_id,
            )
        size = resolved.stat().st_size
        expected_size = int(job.data.get("video_size_bytes") or 0)
        expected_sha = str(job.data.get("checksum_sha256") or "").lower()
        actual_sha = sha256_file(resolved)
        if size != expected_size or not expected_sha or actual_sha != expected_sha:
            raise CDHAUploadError(
                "Local video size or SHA-256 does not match persisted metadata",
                retryable=False,
                manual_action_required=True,
                phase="CDHA_UPLOADING",
                operation="validate_video_metadata",
                job_id=job_id,
                details={
                    "expected_size_bytes": expected_size,
                    "actual_size_bytes": size,
                    "expected_sha256_prefix": expected_sha[:12],
                    "actual_sha256_prefix": actual_sha[:12],
                },
            )
        return size, actual_sha

    @staticmethod
    def _find_cdp_marked_backend_node(
        node: dict[str, Any], marker: str
    ) -> int | None:
        attributes = list(node.get("attributes") or [])
        pairs = dict(zip(attributes[::2], attributes[1::2], strict=False))
        if pairs.get("data-cdha-upload-token") == marker:
            backend_id = node.get("backendNodeId")
            return int(backend_id) if backend_id is not None else None
        nested: list[dict[str, Any]] = list(node.get("children") or [])
        content_document = node.get("contentDocument")
        if isinstance(content_document, dict):
            nested.append(content_document)
        nested.extend(node.get("shadowRoots") or [])
        for child in nested:
            found = CDHAWebClient._find_cdp_marked_backend_node(child, marker)
            if found is not None:
                return found
        return None

    async def _set_large_file_input_via_cdp(
        self, page: Any, input_locator: Any, file_path: Path
    ) -> None:
        host = str(self.settings.browser_cdp_host or "").strip().casefold()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise CDHAUploadError(
                "Large-file direct-path upload requires Chrome on the same host",
                retryable=False,
                manual_action_required=True,
                phase="CDHA_UPLOADING",
                operation="set_large_file_input_via_cdp",
            )
        resolved = Path(file_path).resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.R_OK):
            raise CDHAUploadError(
                "Large-file direct path is not a regular file",
                retryable=False,
                manual_action_required=True,
                phase="CDHA_UPLOADING",
                operation="set_large_file_input_via_cdp",
            )
        context = getattr(page, "context", None)
        if callable(context):
            context = context()
        if context is None or not hasattr(context, "new_cdp_session"):
            raise CDHAUploadError(
                "Existing browser context cannot create a CDP session",
                retryable=False,
                manual_action_required=True,
                phase="CDHA_UPLOADING",
                operation="set_large_file_input_via_cdp",
            )
        marker = uuid.uuid4().hex
        session: Any = None
        try:
            await input_locator.evaluate(
                "(element, token) => element.setAttribute('data-cdha-upload-token', token)",
                marker,
            )
            cdp_target = page
            if hasattr(input_locator, "element_handle"):
                handle = await input_locator.element_handle()
                if handle is not None and hasattr(handle, "owner_frame"):
                    owner_frame = await handle.owner_frame()
                    if owner_frame is not None:
                        cdp_target = owner_frame
            session = await context.new_cdp_session(cdp_target)
            document = await session.send(
                "DOM.getDocument", {"depth": -1, "pierce": True}
            )
            backend_node_id = self._find_cdp_marked_backend_node(
                document["root"], marker
            )
            if backend_node_id is None:
                raise CDHAUploadError(
                    "CDP could not resolve the marked file input node",
                    retryable=False,
                    manual_action_required=True,
                    phase="CDHA_UPLOADING",
                    operation="set_large_file_input_via_cdp",
                )
            await session.send(
                "DOM.setFileInputFiles",
                {"files": [str(resolved)], "backendNodeId": backend_node_id},
            )
        finally:
            try:
                await input_locator.evaluate(
                    "element => element.removeAttribute('data-cdha-upload-token')"
                )
            except Exception:
                pass
            if session is not None and hasattr(session, "detach"):
                await session.detach()

    async def _upload_video_file(
        self,
        page: Any,
        file_input: Any,
        video: Path,
        *,
        job_id: str | None = None,
    ) -> None:
        state = await self._reconcile_existing_upload(page, video)
        if state == "complete":
            return
        if state in {"in_progress", "uncertain"}:
            raise CDHAUploadError(
                f"CDHA upload is {state}; automatic re-upload is blocked",
                error_code="CDHA_UPLOAD_UNCERTAIN",
                retryable=False,
                manual_action_required=True,
                phase="CDHA_UPLOADING",
                operation="upload_video_file",
                details={"upload_state": state},
            )
        resolved = Path(video).resolve()
        size = resolved.stat().st_size
        digest = ""
        if job_id:
            size, digest = self._validate_video_metadata(job_id, resolved)
            self.repository.update_data(
                job_id,
                {
                    "cdha_checkpoint": CDHACheckpoint.UPLOAD_IN_PROGRESS.value,
                    "cdha_submission_state": "SUBMITTING",
                },
            )
        threshold = int(self.settings.cdha_large_file_threshold_mb * 1024 * 1024)
        if size > threshold:
            if not job_id:
                raise CDHAUploadError(
                    "Large CDHA upload requires a durable job ID",
                    retryable=False,
                    manual_action_required=True,
                    phase="CDHA_UPLOADING",
                    operation="upload_video_file",
                )
            job = self.repository.get_job(job_id)
            approval = (job.data if job else {}).get(LargeUploadApproval.DATA_KEY)
            if not job or not LargeUploadApproval.matches(
                job.data,
                job_id=job_id,
                sha256=digest,
                size_bytes=size,
            ):
                raise CDHAUploadError(
                    "Large CDHA upload lacks a matching one-shot approval",
                    error_code="CDHA_LARGE_UPLOAD_APPROVAL_REQUIRED",
                    retryable=False,
                    manual_action_required=True,
                    phase="CDHA_UPLOADING",
                    operation="upload_video_file",
                    job_id=job_id,
                )
            self.repository.update_data(
                job_id,
                {
                    LargeUploadApproval.DATA_KEY: LargeUploadApproval.consumed_data(
                        approval
                    )
                },
            )
            await self._set_large_file_input_via_cdp(page, file_input, resolved)
            self.logger.info(
                "Attached large CDHA upload through local direct-path CDP",
                extra={
                    "job_id": job_id,
                    "size_bytes": size,
                    "sha256_prefix": digest[:12],
                    "strategy": "DOM.setFileInputFiles",
                    "checkpoint": CDHACheckpoint.UPLOAD_IN_PROGRESS.value,
                },
            )
        else:
            await file_input.set_input_files(str(resolved))
        # We no longer wait for upload acknowledgement here because CDHA uses a 2-step process
        # where the file is processed locally first, then uploaded when we click btnComplete.

    async def _request_analysis_once(
        self,
        page: Any,
        *,
        job_id: str,
        diagnostics_dir: Path | None = None,
    ) -> None:
        job = self.repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        checkpoint = CDHACheckpoint.from_data(job.data)
        if checkpoint is not CDHACheckpoint.UPLOAD_CONFIRMED:
            message = (
                "Analyze requires durable UPLOAD_CONFIRMED"
                if checkpoint is CDHACheckpoint.UPLOAD_IN_PROGRESS
                else "Analyze was already requested; reconciliation is required"
            )
            raise CDHAUploadError(
                message,
                error_code="CDHA_SUBMISSION_UNCERTAIN",
                retryable=False,
                manual_action_required=True,
                phase="CDHA_ANALYZING",
                operation="request_analysis_once",
                job_id=job_id,
                details={"current_cdha_state": checkpoint.value},
            )
        button = await self.resolver.find_first(
            page,
            "cdha.analyze_button",
            timeout_ms=int(self.settings.browser_action_timeout_seconds * 1000),
            diagnostics_dir=diagnostics_dir,
            context=f"job_id={job_id} checkpoint={checkpoint.value} action=start_analysis",
        )
        if hasattr(button, "is_visible") and not await button.is_visible():
            raise CDHAUploadError("Analyze is not visible", phase="CDHA_ANALYZING")
        if hasattr(button, "is_enabled") and not await button.is_enabled():
            raise CDHAUploadError("Analyze is not enabled", phase="CDHA_ANALYZING")
        await button.click(
            trial=True,
            timeout=int(self.settings.browser_action_timeout_seconds * 1000),
        )
        self.repository.update_data(
            job_id,
            {
                "cdha_checkpoint": CDHACheckpoint.ANALYSIS_REQUESTED.value,
                "cdha_analysis_requested_at": datetime.now(UTC).isoformat(),
            },
        )
        await button.click(
            timeout=int(self.settings.browser_action_timeout_seconds * 1000)
        )

    async def _upload_stability_signals(
        self, page: Any, video: Path
    ) -> dict[str, bool]:
        view_url = await self._view_url_value(page)
        frame: Any = None
        try:
            frame = await self._resolve_upload_frame(
                page, timeout_ms=500, visible=False
            )
        except Exception:
            pass
        upload_complete = False
        upload_in_progress = False
        filename_matches = False
        if frame is not None:
            upload_complete = await self.resolver.exists(
                frame, "cdha.upload_complete", timeout_ms=300
            )
            upload_in_progress = await self.resolver.exists(
                frame, "cdha.upload_started", timeout_ms=300
            )
            filename = await self._optional_text(frame, "cdha.upload_filename")
            filename_matches = bool(
                filename and video.name.casefold() in filename.casefold()
            )
        analyze_actionable = False
        try:
            analyze = await self.resolver.find_first(
                page, "cdha.analyze_button", timeout_ms=500
            )
            visible = not hasattr(analyze, "is_visible") or await analyze.is_visible()
            enabled = not hasattr(analyze, "is_enabled") or await analyze.is_enabled()
            if visible and enabled:
                await analyze.click(trial=True, timeout=500)
                analyze_actionable = True
        except Exception:
            analyze_actionable = False
        return {
            "completion": bool(view_url or upload_complete),
            "file_identity": bool(view_url or filename_matches),
            "progress_absent": not upload_in_progress,
            "analyze_actionable": analyze_actionable,
        }

    async def _wait_for_stable_upload_completion(
        self, page: Any, video: Path, *, job_id: str
    ) -> None:
        deadline = time.monotonic() + self.settings.cdha_upload_timeout_seconds
        stable_observations = 0
        final_signals: dict[str, bool] = {}
        while time.monotonic() < deadline:
            final_signals = await self._upload_stability_signals(page, video)
            if all(final_signals.values()):
                stable_observations += 1
                if stable_observations >= 2:
                    self.repository.update_data(
                        job_id,
                        {
                            "cdha_checkpoint": CDHACheckpoint.UPLOAD_CONFIRMED.value,
                            "cdha_submission_state": "UPLOADED",
                            "cdha_upload_completed_at": datetime.now(UTC).isoformat(),
                            "cdha_upload_confirmation_signals": final_signals,
                        },
                    )
                    return
            else:
                stable_observations = 0
            await asyncio.sleep(
                min(
                    max(0.001, float(self.settings.cdha_poll_interval_seconds)),
                    max(0.0, deadline - time.monotonic()),
                )
            )
        raise CDHAUploadError(
            "CDHA upload was submitted but stable multi-signal completion was not confirmed",
            error_code="CDHA_SUBMISSION_UNCERTAIN",
            retryable=False,
            manual_action_required=True,
            phase="CDHA_UPLOADING",
            operation="wait_for_stable_upload_completion",
            job_id=job_id,
            details={
                "current_cdha_state": CDHACheckpoint.UPLOAD_IN_PROGRESS.value,
                "completion_signals": final_signals,
            },
        )

    async def _completion_control_snapshot(
        self, page: Any, frame: Any
    ) -> CDHAStateSnapshot:
        get_health = getattr(self.chrome, "get_health", None)
        if get_health is not None:
            health = await get_health(page)
            if health.state.value == "PAGE_CLOSED":
                return CDHAStateSnapshot(CDHAState.PAGE_CLOSED)
            if health.state.value in {"DISCONNECTED", "CONTEXT_CLOSED"}:
                return CDHAStateSnapshot(
                    CDHAState.BROWSER_DISCONNECTED,
                    details={"browser_health_state": health.state.value},
                )
        if await self._view_url_value(page):
            return CDHAStateSnapshot(CDHAState.UPLOAD_COMPLETED)
        probe = getattr(self.resolver, "probe", None)
        if probe is None:
            button = await self.resolver.find_first(
                frame,
                "cdha.upload_complete_button",
                timeout_ms=int(self.settings.browser_action_timeout_seconds * 1000),
            )
            enabled = (
                bool(await button.is_enabled())
                if hasattr(button, "is_enabled")
                else True
            )
            return CDHAStateSnapshot(
                CDHAState.UPLOAD_READY if enabled else CDHAState.CONTROL_DISABLED
            )
        observations = await probe(
            frame,
            "cdha.upload_complete_button",
            timeout_ms=min(
                1_000, int(self.settings.browser_action_timeout_seconds * 1000)
            ),
        )
        attempted = tuple(item.to_dict() for item in observations)
        if any(
            item.attached and item.visible and item.enabled is not False
            for item in observations
        ):
            return CDHAStateSnapshot(
                CDHAState.UPLOAD_READY, selector_attempts=attempted
            )
        if any(item.attached and item.visible for item in observations):
            return CDHAStateSnapshot(
                CDHAState.CONTROL_DISABLED, selector_attempts=attempted
            )
        if any(item.attached for item in observations):
            return CDHAStateSnapshot(
                CDHAState.CONTROL_HIDDEN, selector_attempts=attempted
            )
        return CDHAStateSnapshot(
            CDHAState.CONTROL_NOT_FOUND, selector_attempts=attempted
        )

    async def _complete_upload(
        self,
        page: Any,
        *,
        job_id: str | None = None,
        submission_fingerprint: str | None = None,
    ) -> None:
        if await self._view_url_value(page):
            return
        frame = await self._resolve_upload_frame(page, timeout_ms=5_000)
        try:
            snapshot = await wait_for_cdha_state(
                lambda: self._completion_control_snapshot(page, frame),
                accepted_states={
                    CDHAState.UPLOAD_READY,
                    CDHAState.UPLOAD_COMPLETED,
                },
                timeout_seconds=self.settings.cdha_upload_timeout_seconds,
                poll_interval_seconds=self.settings.cdha_poll_interval_seconds,
                job_id=job_id,
                phase="CDHA_UPLOADING",
                operation="wait_for_upload_completion_control",
                on_progress=lambda state: self.logger.info(
                    "CDHA upload control state changed",
                    extra={
                        "job_id": job_id,
                        "cdha_state": state.state.value,
                    },
                ),
            )
        except CDHAStateTimeoutError as exc:
            details = {
                **exc.details,
                "selector_attempts": [
                    dict(item) for item in exc.final_snapshot.selector_attempts
                ],
            }
            if exc.final_snapshot.state is CDHAState.CONTROL_HIDDEN:
                raise CDHAControlHiddenError(
                    "CDHA completion control remained hidden before timeout",
                    phase="CDHA_UPLOADING",
                    operation="complete_upload",
                    job_id=job_id,
                    details=details,
                ) from exc
            if exc.final_snapshot.state is CDHAState.CONTROL_DISABLED:
                raise CDHAControlDisabledError(
                    "CDHA completion control remained disabled before timeout",
                    phase="CDHA_UPLOADING",
                    operation="complete_upload",
                    job_id=job_id,
                    details=details,
                ) from exc
            raise CDHASelectorMismatchError(
                "CDHA completion control was not found before timeout",
                phase="CDHA_UPLOADING",
                operation="complete_upload",
                job_id=job_id,
                details=details,
            ) from exc
        if snapshot.state is CDHAState.UPLOAD_COMPLETED:
            return
        button = await self.resolver.find_first(
            frame,
            "cdha.upload_complete_button",
            timeout_ms=int(self.settings.browser_action_timeout_seconds * 1000),
        )
        if hasattr(button, "is_enabled") and not await button.is_enabled():
            raise CDHAControlDisabledError(
                "CDHA upload completion control became disabled before click",
                phase="CDHA_UPLOADING",
                operation="complete_upload",
                job_id=job_id,
            )
        if job_id:
            self.repository.update_data(
                job_id,
                {
                    "cdha_submission_state": "SUBMITTING",
                    "cdha_submission_fingerprint": submission_fingerprint,
                    "cdha_submission_started_at": datetime.now(UTC).isoformat(),
                },
            )
        await button.click(
            timeout=int(self.settings.browser_action_timeout_seconds * 1000)
        )
        view_url = await self._wait_for_upload(page)
        if job_id and view_url:
            self.repository.update_data(
                job_id,
                {
                    "cdha_submission_state": "UPLOADED",
                    "cdha_upload_url": view_url,
                    "cdha_upload_completed_at": datetime.now(UTC).isoformat(),
                },
            )

    async def _prepare_video_upload(
        self,
        page: Any,
        video: Path,
        *,
        job_id: str,
        diagnostics_dir: Path,
        submission_fingerprint: str,
    ) -> None:
        file_input = await self._ensure_upload_dialog_open(
            page, job_id=job_id, diagnostics_dir=diagnostics_dir
        )
        await self._upload_video_file(
            page, file_input, video, job_id=job_id
        )
        await self._complete_upload(
            page,
            job_id=job_id,
            submission_fingerprint=submission_fingerprint,
        )
        await self._wait_for_stable_upload_completion(
            page, video, job_id=job_id
        )

    async def _wait_for_upload(self, page: Any) -> str:
        deadline = time.monotonic() + self.settings.cdha_upload_timeout_seconds
        upload_started = False
        try:
            frame = await self._resolve_upload_frame(page, timeout_ms=1000, visible=False)
        except Exception:
            frame = page
        while time.monotonic() < deadline:
            view_url = await self._view_url_value(page)
            if view_url:
                self.logger.info("CDHA upload completed; view URL is available")
                return view_url
            if await self.resolver.exists(frame, "cdha.upload_error", timeout_ms=500):
                message = await self._optional_text(frame, "cdha.upload_error")
                raise CDHAUploadError(
                    f"CDHA upload failed: {message or 'file rejected'}",
                    retryable=False,
                    phase="CDHA_UPLOADING",
                    operation="wait_for_upload_completion",
                )
            if await self.resolver.exists(frame, "cdha.upload_started", timeout_ms=500):
                upload_started = True
            if await self.resolver.exists(frame, "cdha.upload_complete", timeout_ms=500):
                return ""
            await asyncio.sleep(min(1.0, self.settings.cdha_poll_interval_seconds))
        suffix = " after starting" if upload_started else " (upload start was not detected)"
        raise CDHAUploadError(
            "CDHA upload outcome is uncertain after Complete" + suffix,
            error_code="CDHA_UPLOAD_UNCERTAIN",
            retryable=False,
            manual_action_required=True,
            phase="CDHA_UPLOADING",
            operation="wait_for_upload_completion",
        )

    async def _detect_analysis_state(self, page: Any) -> CDHAStateSnapshot:
        get_health = getattr(self.chrome, "get_health", None)
        if get_health is not None:
            health = await get_health(page)
            if health.state.value == "PAGE_CLOSED":
                return CDHAStateSnapshot(CDHAState.PAGE_CLOSED)
            if health.state.value in {"DISCONNECTED", "CONTEXT_CLOSED"}:
                return CDHAStateSnapshot(
                    CDHAState.BROWSER_DISCONNECTED,
                    details={"browser_health_state": health.state.value},
                )
        try:
            if hasattr(page, "is_closed") and bool(page.is_closed()):
                return CDHAStateSnapshot(CDHAState.PAGE_CLOSED)
        except Exception:
            return CDHAStateSnapshot(CDHAState.PAGE_CLOSED)
        url = str(getattr(page, "url", "") or "")
        authentication = await self.detect_authentication_state(page)
        if authentication in {
            AuthenticationState.LOGIN_REQUIRED,
            AuthenticationState.TWO_FACTOR_REQUIRED,
            AuthenticationState.CHECKPOINT_REQUIRED,
            AuthenticationState.SESSION_EXPIRED,
        }:
            return CDHAStateSnapshot(
                CDHAState.LOGIN_REQUIRED,
                current_url=url,
                details={"authentication_state": authentication.value},
            )
        if authentication is AuthenticationState.PERMISSION_DENIED:
            return CDHAStateSnapshot(
                CDHAState.PERMISSION_DENIED,
                current_url=url,
                details={"authentication_state": authentication.value},
            )
        if await self.resolver.exists(page, "cdha.error_message", timeout_ms=300):
            message = await self._optional_text(page, "cdha.error_message")
            return CDHAStateSnapshot(
                CDHAState.ANALYSIS_FAILED,
                current_url=url,
                details={"message": message or "CDHA error message displayed"},
            )
        if await self.resolver.exists(page, "cdha.result_container", timeout_ms=300):
            result = await self._optional_text(page, "cdha.result_container")
            if result:
                return CDHAStateSnapshot(CDHAState.RESULT_READY, current_url=url)
        if await self.resolver.exists(page, "cdha.analysis_complete", timeout_ms=300):
            return CDHAStateSnapshot(
                CDHAState.ANALYSIS_COMPLETED, current_url=url
            )
        if "view=" in url:
            return CDHAStateSnapshot(
                CDHAState.ANALYSIS_COMPLETED, current_url=url
            )
        if await self.resolver.exists(page, "cdha.analysis_started", timeout_ms=300):
            return CDHAStateSnapshot(CDHAState.ANALYSIS_RUNNING, current_url=url)
        try:
            if await self.resolver.exists(
                page, "cdha.analysis_queued", timeout_ms=300
            ):
                return CDHAStateSnapshot(
                    CDHAState.ANALYSIS_QUEUED, current_url=url
                )
        except KeyError:
            pass
        return CDHAStateSnapshot(CDHAState.UNKNOWN, current_url=url)

    async def _wait_for_analysis(
        self, page: Any, *, job_id: str | None = None
    ) -> None:
        terminal_states = {
            CDHAState.RESULT_READY,
            CDHAState.ANALYSIS_COMPLETED,
            CDHAState.ANALYSIS_FAILED,
            CDHAState.LOGIN_REQUIRED,
            CDHAState.PERMISSION_DENIED,
            CDHAState.PAGE_CLOSED,
            CDHAState.BROWSER_DISCONNECTED,
        }
        try:
            snapshot = await wait_for_cdha_state(
                lambda: self._detect_analysis_state(page),
                accepted_states=terminal_states,
                timeout_seconds=self.settings.cdha_analysis_timeout_seconds,
                poll_interval_seconds=self.settings.cdha_poll_interval_seconds,
                job_id=job_id,
                on_progress=lambda state: self.logger.info(
                    "CDHA analysis state changed",
                    extra={"job_id": job_id, "cdha_state": state.state.value},
                ),
            )
        except CDHAStateTimeoutError as exc:
            if exc.final_snapshot.state is CDHAState.UNKNOWN:
                raise CDHAStateTimeoutError(
                    "CDHA analysis did not start before timeout",
                    final_snapshot=exc.final_snapshot,
                    timeout_seconds=self.settings.cdha_analysis_timeout_seconds,
                    job_id=job_id,
                ) from exc
            raise
        if snapshot.state is CDHAState.RESULT_READY:
            return
        if snapshot.state is CDHAState.ANALYSIS_COMPLETED:
            current_url = str(snapshot.current_url or "")
            external_id = dict(
                parse_qsl(urlsplit(current_url).query, keep_blank_values=True)
            ).get("view")
            if external_id:
                return
        if snapshot.state is CDHAState.ANALYSIS_FAILED:
            raise CDHARenderError(
                str(snapshot.details.get("message") or "CDHA analysis failed"),
                retryable=False,
                phase="CDHA_ANALYZING",
                operation="wait_for_analysis",
                job_id=job_id,
                details={"current_cdha_state": snapshot.state.value},
            )
        if snapshot.state in {
            CDHAState.LOGIN_REQUIRED,
            CDHAState.PERMISSION_DENIED,
        }:
            raise CDHAAuthenticationRequiredError(
                "CDHA authentication or permission is required during analysis",
                phase="CDHA_ANALYZING",
                operation="wait_for_analysis",
                job_id=job_id,
                details={
                    "current_cdha_state": snapshot.state.value,
                    **snapshot.details,
                },
            )
        if snapshot.state is CDHAState.PAGE_CLOSED:
            raise BrowserPageClosedError(
                "CDHA page closed during analysis",
                phase="CDHA_ANALYZING",
                operation="wait_for_analysis",
                job_id=job_id,
                details={"current_cdha_state": snapshot.state.value},
            )
        if snapshot.state is CDHAState.BROWSER_DISCONNECTED:
            error_type = (
                BrowserContextClosedError
                if snapshot.details.get("browser_health_state")
                == "CONTEXT_CLOSED"
                else BrowserDisconnectedError
            )
            raise error_type(
                "CDHA shared browser became unavailable during analysis",
                phase="CDHA_ANALYZING",
                operation="wait_for_analysis",
                job_id=job_id,
                details={
                    "current_cdha_state": snapshot.state.value,
                    **snapshot.details,
                },
            )
        await wait_for_cdha_state(
            lambda: self._detect_analysis_state(page),
            accepted_states={CDHAState.RESULT_READY},
            timeout_seconds=self.settings.cdha_result_timeout_seconds,
            poll_interval_seconds=self.settings.cdha_poll_interval_seconds,
            job_id=job_id,
            phase="CDHA_ANALYZING",
            operation="wait_for_result_ready",
        )

    async def extract_result(
        self, page: Any, job_id: str, started_at: str = ""
    ) -> CDHAAnalysisResult:
        warnings: list[str] = []
        raw_text = await self._optional_text(page, "cdha.result_container") or ""
        triage = await self._field(page, "cdha.triage", "triage", warnings)
        confidence = await self._field(page, "cdha.confidence", "confidence", warnings)
        findings_text = await self._field(page, "cdha.key_findings", "key_findings", warnings)
        impression_text = await self._field(page, "cdha.impression", "impression", warnings)
        detailed = await self._field(
            page, "cdha.detailed_analysis", "detailed_analysis", warnings
        )
        regions_text = await self._field(page, "cdha.marked_regions", "marked_regions", warnings)
        return CDHAAnalysisResult(
            success=True,
            job_id=job_id,
            triage=triage,
            confidence=confidence,
            key_findings=CDHAClinicalSummary.normalize_key_findings(findings_text or ""),
            impression=CDHAClinicalSummary.normalize_impression_text(impression_text or "") or None,
            analysis_url=str(getattr(page, "url", "") or ""),
            raw_key_findings=findings_text,
            raw_impression=impression_text,
            detailed_analysis=detailed,
            marked_regions=self._split_lines(regions_text),
            raw_text=raw_text,
            started_at=started_at,
            warnings=warnings,
        )

    async def _field(
        self, page: Any, selector_key: str, field_name: str, warnings: list[str]
    ) -> str | None:
        try:
            locator = await self.resolver.find_first(page, selector_key, timeout_ms=1_200)
            value = await self._field_locator_text(locator)
        except (SelectorResolutionError, KeyError, AttributeError):
            value = None
        if not value:
            warnings.append(f"CDHA result field could not be extracted: {field_name}")
            return None
        return value

    @staticmethod
    async def _field_locator_text(locator: Any) -> str | None:
        direct = (await locator.inner_text()).strip()
        try:
            contextual = await locator.evaluate(
                r"""element => {
                    const text = node => (node?.innerText || node?.textContent || '').trim();
                    const own = text(element);
                    const normalized = own.toLocaleLowerCase().replace(/[:\s]+$/g, '');
                    const labels = new Set([
                        'key findings', 'findings', 'phát hiện chính', 'ghi nhận chính',
                        'impression', 'nhận định', 'kết luận', 'triage', 'phân loại',
                        'confidence', 'độ tin cậy', 'phân tích chi tiết',
                        'detailed analysis', 'marked regions', 'vùng được đánh dấu'
                    ]);
                    if (!labels.has(normalized)) return own;
                    const values = [];
                    let sibling = element.nextElementSibling;
                    while (sibling) {
                        if (/^H[1-6]$/.test(sibling.tagName)) break;
                        const value = text(sibling);
                        if (value) values.push(value);
                        sibling = sibling.nextElementSibling;
                    }
                    if (values.length) return own + '\n' + values.join('\n');
                    const parent = element.parentElement;
                    const parentText = text(parent);
                    return parentText && parentText !== own ? parentText : own;
                }"""
            )
            return str(contextual or direct).strip() or None
        except (AttributeError, TypeError):
            return direct or None

    async def _optional_text(self, page: Any, selector_key: str) -> str | None:
        try:
            locator = await self.resolver.find_first(page, selector_key, timeout_ms=1_200)
            return (await locator.inner_text()).strip() or None
        except (SelectorResolutionError, KeyError, AttributeError):
            return None

    @staticmethod
    async def _input_text(locator: Any) -> str:
        try:
            return await locator.input_value()
        except Exception:
            return (await locator.text_content() or "").strip()

    @staticmethod
    def _split_lines(value: str | None) -> list[str]:
        return [line.strip(" -•\t") for line in (value or "").splitlines() if line.strip(" -•\t")]

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        path.chmod(0o600)

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
        path.chmod(0o600)
