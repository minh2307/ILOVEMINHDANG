from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.browser.chrome_manager import ChromeManager
from app.browser.selector_resolver import SelectorResolutionError, SelectorResolver
from app.config.settings import Settings
from app.models.results import CDHAAnalysisResult
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.privacy_service import PrivacyService
from app.services.screenshot_service import ScreenshotService
from app.browser.error_mapper import map_playwright_error
from app.error_events import build_error_event_details
from app.errors import (
    AuthenticationRequiredError,
    CDHARenderError,
    CDHAUploadError,
    FrameNotReadyError,
    PipelineError,
    SelectorNotFoundError,
)
from app.services.retry_service import RetryAttempt, RetryPolicy, retry_async


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
            page = await self.chrome.new_page()
            await page.goto(
                self.settings.cdha_url,
                wait_until="domcontentloaded",
                timeout=self.settings.page_timeout_seconds * 1000,
            )
            if not await self.is_authenticated(page):
                current = self.repository.get_job(job_id)
                if current and current.status is not WorkflowStatus.NEEDS_CDHA_LOGIN:
                    self.repository.transition(
                        job_id,
                        WorkflowStatus.NEEDS_CDHA_LOGIN,
                        details={"reason": "login_or_manual_security_action_required"},
                    )
                await self.chrome.wait_for_manual_action(
                    "Complete CDHA login, 2FA, CAPTCHA, or account verification manually. "
                    "No challenge will be bypassed.",
                    lambda: self.is_authenticated(page),
                )
            if not await self.is_authenticated(page):
                raise AuthenticationRequiredError(
                    "CDHA authenticated page was not verified",
                    phase="CDHA_OPENING", operation="authenticate", job_id=job_id,
                )
            if "modality=us_video" not in str(page.url) and not await self.resolver.exists(
                page, "cdha.modality_marker", timeout_ms=2_000
            ):
                raise RuntimeError("CDHA ultrasound-video modality could not be verified")

            view_url = job.data.get("cdha_view_url")
            if not view_url:
                video = self.validate_video_path(video_path)
                self.repository.transition(
                    job_id,
                    WorkflowStatus.CDHA_UPLOADING,
                    details={"video_path": str(video)},
                )
                self.logger.info("Uploading local video file via CDHA iframe", extra={"job_id": job_id})
                
                await self._prepare_video_upload(
                    page, video, job_id=job_id, diagnostics_dir=diagnostics_dir
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
                analyze_button = await self.resolver.find_first(
                    page,
                    "cdha.analyze_button",
                    timeout_ms=10_000,
                    diagnostics_dir=diagnostics_dir,
                    context=f"job_id={job_id} state=CDHA_UPLOADING action=start_analysis",
                )
                await analyze_button.click()
                self.repository.transition(
                    job_id,
                    WorkflowStatus.CDHA_ANALYZING,
                    details={"action": "analysis_started"},
                )
                await self._wait_for_analysis(page)
                extracted = await self.extract_result(page, job_id, started_at)
            else:
                self.logger.info(f"CDHA analysis already completed, jumping to result URL: {view_url}", extra={"job_id": job_id})
                # Satisfy state machine
                self.repository.transition(job_id, WorkflowStatus.CDHA_UPLOADING, details={"skipped": True})
                self.repository.transition(job_id, WorkflowStatus.CDHA_ANALYZING, details={"skipped": True})
                
                await page.goto(view_url)
                extracted = await self.extract_result(page, job_id, started_at)
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
                    "cdha_view_url": page.url,
                    "clinical_factors_path": str(masked_factors_path),
                    "clinical_factors": factors,
                    "cdha_completed_at": completed_at,
                },
            )

            self.repository.transition(
                job_id,
                WorkflowStatus.SCREENSHOTS_CAPTURING,
                details={"action": "capture"},
            )
            screenshot_paths, screenshot_warnings = await self.screenshots.capture_required(
                page, job_dir
            )
            all_warnings = [*extracted.warnings, *screenshot_warnings]
            final_result = CDHAAnalysisResult(
                success=True,
                job_id=job_id,
                triage=extracted.triage,
                confidence=extracted.confidence,
                key_findings=extracted.key_findings,
                impression=extracted.impression,
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
            failure_diagnostics: tuple[Path, Path] | None = None
            if page is not None:
                try:
                    failure_diagnostics = await self.chrome.save_diagnostics(
                        page, diagnostics_dir, "cdha-failure"
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
                    failure_patch["cdha_failure_screenshot_path"] = str(failure_diagnostics[0])
                    if self.settings.save_diagnostic_html:
                        failure_patch["cdha_failure_html_path"] = str(failure_diagnostics[1])
                self.repository.transition(
                    job_id, WorkflowStatus.CDHA_FAILED, details=details, data_patch=failure_patch
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

    async def is_authenticated(self, page: Any) -> bool:
        url = str(getattr(page, "url", "")).casefold()
        if any(marker in url for marker in ("/login", "/signin", "auth.")):
            return False
        if await self.resolver.exists(page, "cdha.login_markers", timeout_ms=800):
            return False
        if await self.resolver.exists(page, "cdha.security_markers", timeout_ms=800):
            return False
        return await self.resolver.exists(page, "cdha.authenticated_marker", timeout_ms=1_500)

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

    async def _resolve_upload_frame(self, page: Any, *, timeout_ms: int = 2_000) -> Any:
        failures: list[str] = []
        selectors = self._css_candidates("cdha.upload_frame")
        per_selector_timeout = max(250, timeout_ms // len(selectors))
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                await locator.wait_for(state="attached", timeout=per_selector_timeout)
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
        frame = await self._resolve_upload_frame(page, timeout_ms=timeout_ms)
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
        if await self.resolver.exists(page, "cdha.upload_complete", timeout_ms=400):
            return "complete"
        if await self.resolver.exists(page, "cdha.upload_started", timeout_ms=400):
            return "in_progress"
        filename = await self._optional_text(page, "cdha.upload_filename")
        if filename and video.name.casefold() in filename.casefold():
            return "uncertain"
        return "not_started"

    async def _wait_for_upload_acknowledgement(self, page: Any) -> None:
        deadline = time.monotonic() + self.settings.page_timeout_seconds
        while time.monotonic() < deadline:
            if await self.resolver.exists(page, "cdha.upload_error", timeout_ms=400):
                message = await self._optional_text(page, "cdha.upload_error")
                raise CDHAUploadError(
                    f"CDHA upload failed: {message or 'file rejected'}",
                    phase="CDHA_UPLOADING",
                    operation="wait_for_upload_acknowledgement",
                    retryable=False,
                )
            if await self._view_url_value(page):
                return
            if await self.resolver.exists(page, "cdha.upload_started", timeout_ms=400):
                return
            if await self.resolver.exists(page, "cdha.upload_complete", timeout_ms=400):
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

    async def _upload_video_file(self, page: Any, file_input: Any, video: Path) -> None:
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
        await file_input.set_input_files(str(video))
        await self._wait_for_upload_acknowledgement(page)

    async def _complete_upload(self, page: Any) -> None:
        if await self._view_url_value(page):
            return
        frame = await self._resolve_upload_frame(page, timeout_ms=5_000)
        button = await self.resolver.find_first(
            frame, "cdha.upload_complete_button", timeout_ms=10_000
        )
        if hasattr(button, "is_enabled") and not await button.is_enabled():
            raise CDHAUploadError(
                "CDHA upload Complete button is disabled",
                phase="CDHA_UPLOADING",
                operation="complete_upload",
            )
        await button.click(timeout=10_000)
        await self._wait_for_upload(page)

    async def _prepare_video_upload(
        self, page: Any, video: Path, *, job_id: str, diagnostics_dir: Path
    ) -> None:
        file_input = await self._ensure_upload_dialog_open(
            page, job_id=job_id, diagnostics_dir=diagnostics_dir
        )
        await self._upload_video_file(page, file_input, video)
        await self._complete_upload(page)

    async def _wait_for_upload(self, page: Any) -> None:
        deadline = time.monotonic() + self.settings.upload_timeout_seconds
        upload_started = False
        while time.monotonic() < deadline:
            if await self._view_url_value(page):
                self.logger.info("CDHA upload completed; view URL is available")
                return
            if await self.resolver.exists(page, "cdha.upload_error", timeout_ms=500):
                message = await self._optional_text(page, "cdha.upload_error")
                raise CDHAUploadError(
                    f"CDHA upload failed: {message or 'file rejected'}",
                    retryable=False,
                    phase="CDHA_UPLOADING",
                    operation="wait_for_upload_completion",
                )
            if await self.resolver.exists(page, "cdha.upload_started", timeout_ms=500):
                upload_started = True
            if await self.resolver.exists(page, "cdha.upload_complete", timeout_ms=500):
                return
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

    async def _wait_for_analysis(self, page: Any) -> None:
        deadline = time.monotonic() + self.settings.cdha_analysis_timeout_seconds
        started = False
        stable_since: float | None = None
        previous_result = ""
        while time.monotonic() < deadline:
            if "view=" in page.url:
                self.logger.info("Results page detected via URL, marking analysis as complete", extra={"job_id": "unknown"})
                return
            if await self.resolver.exists(page, "cdha.error_message", timeout_ms=500):
                message = await self._optional_text(page, "cdha.error_message")
                raise RuntimeError(f"CDHA analysis failed: {message or 'error message displayed'}")
            if await self.resolver.exists(page, "cdha.analysis_started", timeout_ms=500):
                started = True
            complete = await self.resolver.exists(page, "cdha.analysis_complete", timeout_ms=500)
            result_visible = await self.resolver.exists(page, "cdha.result_container", timeout_ms=500)
            if complete or result_visible:
                started = True
                current_result = await self._optional_text(page, "cdha.result_container") or ""
                now = time.monotonic()
                if current_result and current_result == previous_result:
                    stable_since = stable_since or now
                    if now - stable_since >= self.settings.cdha_result_stability_seconds:
                        return
                else:
                    previous_result = current_result
                    stable_since = now
                if complete and not current_result:
                    return
            await asyncio.sleep(self.settings.cdha_poll_interval_seconds)
        if not started:
            raise TimeoutError("CDHA analysis did not start before timeout")
        raise TimeoutError("CDHA analysis did not complete before timeout")

    async def extract_result(
        self, page: Any, job_id: str, started_at: str = ""
    ) -> CDHAAnalysisResult:
        warnings: list[str] = []
        raw_text = await self._optional_text(page, "cdha.result_container") or ""
        triage = await self._field(page, "cdha.triage", "triage", warnings)
        confidence = await self._field(page, "cdha.confidence", "confidence", warnings)
        findings_text = await self._field(page, "cdha.key_findings", "key_findings", warnings)
        impression = await self._field(page, "cdha.impression", "impression", warnings)
        detailed = await self._field(
            page, "cdha.detailed_analysis", "detailed_analysis", warnings
        )
        regions_text = await self._field(page, "cdha.marked_regions", "marked_regions", warnings)
        return CDHAAnalysisResult(
            success=True,
            job_id=job_id,
            triage=triage,
            confidence=confidence,
            key_findings=self._split_lines(findings_text),
            impression=impression,
            detailed_analysis=detailed,
            marked_regions=self._split_lines(regions_text),
            raw_text=raw_text,
            started_at=started_at,
            warnings=warnings,
        )

    async def _field(
        self, page: Any, selector_key: str, field_name: str, warnings: list[str]
    ) -> str | None:
        value = await self._optional_text(page, selector_key)
        if not value:
            warnings.append(f"CDHA result field could not be extracted: {field_name}")
            return None
        return value

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
