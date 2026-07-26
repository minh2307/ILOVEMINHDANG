from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.browser.chrome_manager import ChromeManager
from app.browser.selector_resolver import SelectorResolver
from app.config.settings import Settings
from app.error_events import safe_error_message
from app.models.results import ClinicalFactorsResult, NormalizedComment
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.clinical_factors_service import ClinicalFactorsService


class GeminiWebClient:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        chrome: ChromeManager,
        *,
        resolver: SelectorResolver | None = None,
        clinical_factors: ClinicalFactorsService | None = None,
        logger: logging.Logger | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.chrome = chrome
        self.resolver = resolver or SelectorResolver(settings.selectors_path, save_html=settings.save_diagnostic_html)
        self.clinical_factors = clinical_factors or ClinicalFactorsService(
            max_response_chars=settings.clinical_factors_max_chars,
            max_comment_chars=settings.clinical_factors_comment_max_chars,
            max_comments=settings.clinical_factors_max_comments,
            max_total_comment_chars=settings.gemini_comment_total_max_chars,
            max_prompt_chars=settings.gemini_prompt_max_chars,
        )
        self.logger = logger or logging.getLogger("cdha_pipeline.gemini")

    async def generate_clinical_factors(
        self,
        *,
        caption: str,
        comments: list[NormalizedComment] | list[dict[str, Any]],
        job_id: str,
    ) -> ClinicalFactorsResult:
        job = self.repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        if job.status is WorkflowStatus.GEMINI_FAILED:
            job = self.repository.transition(
                job_id, WorkflowStatus.RETRY_PENDING, details={"retry_step": "gemini"}
            )
        if job.status not in {
            WorkflowStatus.DOWNLOADED,
            WorkflowStatus.RETRY_PENDING,
            WorkflowStatus.NEEDS_GEMINI_LOGIN,
        }:
            raise ValueError(
                f"Gemini requires a DOWNLOADED or retryable job, got {job.status.value}"
            )

        opening_at = datetime.now(UTC).isoformat()
        if job.status is not WorkflowStatus.NEEDS_GEMINI_LOGIN:
            self.repository.transition(
                job_id,
                WorkflowStatus.GEMINI_OPENING,
                details={"url": self.settings.gemini_url},
                data_patch={"gemini_error": None, "gemini_started_at": opening_at},
            )
        else:
            self.repository.update_data(
                job_id, {"gemini_error": None, "gemini_resumed_at": opening_at}
            )
        job_dir = (self.settings.job_data_dir / job_id).resolve()
        diagnostics_dir = job_dir / "diagnostics"
        job_dir.mkdir(parents=True, exist_ok=True)
        page: Any = None
        validation_result: ClinicalFactorsResult | None = None
        try:
            page = await self.chrome.new_page()
            await page.goto(
                self.settings.gemini_url,
                wait_until="domcontentloaded",
                timeout=self.settings.page_timeout_seconds * 1000,
            )
            if not await self.is_authenticated(page):
                current = self.repository.get_job(job_id)
                if current and current.status is not WorkflowStatus.NEEDS_GEMINI_LOGIN:
                    self.repository.transition(
                        job_id,
                        WorkflowStatus.NEEDS_GEMINI_LOGIN,
                        details={"reason": "login_or_manual_security_action_required"},
                    )
                await self.chrome.wait_for_manual_action(
                    "Complete Gemini login, 2FA, CAPTCHA, or account verification manually. "
                    "No challenge will be bypassed.",
                    lambda: self.is_authenticated(page),
                )
            if not await self.is_authenticated(page):
                raise RuntimeError("Gemini authenticated page was not verified")

            if await self.resolver.exists(page, "gemini.new_chat", timeout_ms=1_500):
                await self.resolver.click_first(page, "gemini.new_chat", timeout_ms=3_000)
            input_assessment = self.clinical_factors.assess_external_content(caption, comments)
            prompt = self.clinical_factors.build_prompt(caption, comments)
            prompt_path: Path | None = None
            if self.settings.save_raw_gemini_prompt:
                prompt_path = (job_dir / "gemini-prompt.txt").resolve()
                self._write_text_atomic(prompt_path, prompt)
            if input_assessment.risk_level != "LOW":
                self.repository.record_event(
                    job_id,
                    details={
                        "security_warning": "untrusted_gemini_input",
                        "risk_level": input_assessment.risk_level,
                        "suspicious_patterns": list(input_assessment.suspicious_patterns),
                    },
                )
            baseline_response = await self._newest_response_text(page)
            input_locator = await self.resolver.find_first(
                page,
                "gemini.prompt_input",
                timeout_ms=10_000,
                diagnostics_dir=diagnostics_dir,
                context=f"job_id={job_id} state=GEMINI_OPENING action=insert_prompt",
            )
            await input_locator.fill(prompt)
            await self.resolver.click_first(
                page,
                "gemini.send_button",
                timeout_ms=10_000,
                diagnostics_dir=diagnostics_dir,
                context=f"job_id={job_id} state=GEMINI_OPENING action=submit_prompt",
            )
            self.repository.transition(
                job_id,
                WorkflowStatus.GEMINI_GENERATING,
                details={
                    "action": "clinical_factors_prompt_submitted",
                    "input_risk_level": input_assessment.risk_level,
                },
                data_patch={
                    "gemini_prompt_path": str(prompt_path) if prompt_path else None,
                    "gemini_input_risk_level": input_assessment.risk_level,
                    "gemini_input_suspicious_patterns": list(input_assessment.suspicious_patterns),
                    "gemini_input_truncated": input_assessment.truncated,
                },
            )
            raw_response = await self._wait_for_newest_final_response(
                page, previous_response=baseline_response
            )
            source_text = caption + "\n" + "\n".join(
                self.clinical_factors._comment_content(comment) for comment in comments
            )
            result = self.clinical_factors.validate(
                raw_response, job_id=job_id, source_text=source_text
            )
            raw_path: Path | None = None
            if self.settings.save_raw_gemini_response:
                raw_path = (job_dir / "gemini-response-raw.txt").resolve()
                self._write_text_atomic(raw_path, result.raw_response)
            normalized_path = (job_dir / "clinical-factors-normalized.txt").resolve()
            masked_path = (job_dir / "clinical-factors-masked.txt").resolve()
            self._write_text_atomic(normalized_path, result.normalized_text)
            self._write_text_atomic(masked_path, result.masked_text)
            result = replace(
                result,
                raw_response_path=str(raw_path) if raw_path else None,
                normalized_output_path=str(normalized_path),
            )
            validation_result = result
            if not result.success:
                raise RuntimeError(result.error or "Clinical Factors validation failed")
            self.repository.transition(
                job_id,
                WorkflowStatus.CLINICAL_FACTORS_GENERATED,
                details={"clinical_factors_path": str(masked_path)},
                data_patch={
                    "gemini_raw_response_path": str(raw_path) if raw_path else None,
                    "clinical_factors_normalized_path": str(normalized_path),
                    "clinical_factors_path": str(masked_path),
                    "clinical_factors": result.masked_text,
                    "clinical_factors_missing_fields": result.missing_fields,
                    "clinical_factors_generated_at": result.to_dict()["generated_at"],
                    "clinical_factors_warnings": result.validation_warnings,
                    "gemini_completed_at": datetime.now(UTC).isoformat(),
                    "gemini_error": None,
                },
            )
            return result
        except Exception as exc:
            error = safe_error_message(exc)
            if page is not None:
                try:
                    await self.chrome.save_diagnostics(page, diagnostics_dir, "gemini-failure")
                except Exception:
                    self.logger.exception("Failed to save Gemini diagnostics", extra={"job_id": job_id})
            current = self.repository.get_job(job_id)
            if current and current.status in {
                WorkflowStatus.GEMINI_OPENING,
                WorkflowStatus.GEMINI_GENERATING,
            }:
                failure_patch: dict[str, Any] = {
                    "gemini_error": error,
                    "gemini_completed_at": datetime.now(UTC).isoformat(),
                }
                if validation_result is not None:
                    failure_patch.update(
                        {
                            "gemini_raw_response_path": validation_result.raw_response_path,
                            "clinical_factors_normalized_path": validation_result.normalized_output_path,
                            "clinical_factors_missing_fields": validation_result.missing_fields,
                            "clinical_factors_warnings": validation_result.validation_warnings,
                        }
                    )
                self.repository.transition(
                    job_id,
                    WorkflowStatus.GEMINI_FAILED,
                    details={"error": error},
                    data_patch=failure_patch,
                )
            self.logger.error("Gemini step failed", extra={"job_id": job_id, "error": error})
            if validation_result is not None:
                return validation_result
            return ClinicalFactorsResult(success=False, job_id=job_id, error=error)

    async def is_authenticated(self, page: Any) -> bool:
        url = str(getattr(page, "url", "")).casefold()
        if "accounts.google." in url:
            return False
        if await self.resolver.exists(page, "gemini.login_markers", timeout_ms=800):
            return False
        if await self.resolver.exists(page, "gemini.security_markers", timeout_ms=800):
            return False
        return await self.resolver.exists(page, "gemini.authenticated_marker", timeout_ms=10_000)

    async def _wait_for_newest_final_response(
        self, page: Any, *, previous_response: str = ""
    ) -> str:
        deadline = time.monotonic() + self.settings.page_timeout_seconds
        previous = previous_response
        stable_count = 0
        generation_started = False
        while time.monotonic() < deadline:
            if await self.resolver.exists(page, "gemini.error_message", timeout_ms=500):
                raise RuntimeError("Gemini displayed an error message")
            text = await self._newest_response_text(page)
            if text and text != previous_response:
                generation_started = True
                if text == previous:
                    stable_count += 1
                else:
                    previous = text
                    stable_count = 0
                is_stopping = await self.resolver.exists(
                    page, "gemini.stop_button", timeout_ms=500
                )
                if not is_stopping and stable_count >= 2:
                    return text
            await asyncio.sleep(0.5)
        if not generation_started:
            raise TimeoutError("Gemini generation did not start before timeout")
        raise TimeoutError("Gemini generation did not complete before timeout")

    async def _newest_response_text(self, page: Any) -> str:
        for candidate in self.resolver.candidates("gemini.response"):
            try:
                locator = self.resolver._locator(page, candidate)
                count = await locator.count()
                if count:
                    return (await locator.nth(count - 1).inner_text()).strip()
            except Exception:
                continue
        return ""

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        path.chmod(0o600)
