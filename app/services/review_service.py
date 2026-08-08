from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import subprocess
from typing import Callable

from app.config.settings import Settings
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.clinical_factors_service import ClinicalFactorsService
from app.services.post_content_service import PostContentService


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    action: str
    exit_code: int = 0


class ReviewService:
    """Presents the Phase 3 checkpoint without performing any publication."""

    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        clinical_factors: ClinicalFactorsService | None = None,
        post_content: PostContentService | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.clinical_factors = clinical_factors or ClinicalFactorsService(
            max_response_chars=settings.clinical_factors_max_chars,
            max_comment_chars=settings.clinical_factors_comment_max_chars,
            max_comments=settings.clinical_factors_max_comments,
            max_total_comment_chars=settings.gemini_comment_total_max_chars,
            max_prompt_chars=settings.gemini_prompt_max_chars,
        )
        self.post_content = post_content or PostContentService(settings)

    def review(
        self,
        job_id: str,
        *,
        choice_provider: Callable[[str], str] = input,
        edited_text_provider: Callable[[], str] | None = None,
    ) -> ReviewDecision:
        job = self.repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        if job.status is not WorkflowStatus.WAITING_FOR_REVIEW:
            raise ValueError(
                f"--review-job requires WAITING_FOR_REVIEW; got {job.status.value}"
            )
        self.display(job_id)
        if self.settings.auto_approve_review:
            print("\n[AUTO-APPROVE] AUTO_APPROVE_REVIEW is enabled. Auto-accepting...")
            choice = "1"
        else:
            choice = choice_provider("Select [1-7]: ").strip()

        if choice == "1":
            return self.approve(
                job_id, automatic=self.settings.auto_approve_review
            )
        if choice == "2":
            self.repository.transition(
                job_id,
                WorkflowStatus.REJECTED,
                details={"review_decision": "rejected"},
            )
            return ReviewDecision("rejected")
        if choice == "3":
            provider = edited_text_provider or self._read_multiline_edit
            self._save_edited_factors(job_id, provider())
            self.repository.transition(
                job_id,
                WorkflowStatus.RETRY_PENDING,
                details={"review_decision": "clinical_factors_edited"},
                data_patch={"retry_step": "cdha"},
            )
            return ReviewDecision("retry_cdha")
        if choice == "4":
            self.repository.transition(
                job_id,
                WorkflowStatus.RETRY_PENDING,
                details={"review_decision": "retry_ollama"},
                data_patch={
                    "retry_step": "ai_analysis",
                    "clinical_factors_path": None
                },
            )
            return ReviewDecision("retry_ollama")
        if choice == "5":
            self.repository.transition(
                job_id,
                WorkflowStatus.RETRY_PENDING,
                details={"review_decision": "retry_cdha"},
                data_patch={"retry_step": "cdha"},
            )
            return ReviewDecision("retry_cdha")
        if choice == "6":
            folder = (self.settings.job_data_dir / job_id / "screenshots").resolve()
            folder.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.Popen(
                    ["xdg-open", str(folder)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                print(f"Opened screenshot folder: {folder}")
            except OSError as exc:
                print(f"Could not open the folder automatically ({exc}). Folder: {folder}")
            return ReviewDecision("show_screenshot_folder")
        if choice == "7":
            print("Review left pending. Resume later with --review-job.")
        return ReviewDecision("resume_later")

    def approve(self, job_id: str, *, automatic: bool = False) -> ReviewDecision:
        job = self.repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        if job.status is not WorkflowStatus.WAITING_FOR_REVIEW:
            raise ValueError(
                f"Review approval requires WAITING_FOR_REVIEW; got {job.status.value}"
            )
        cdha = job.data.get("cdha_result") or {}
        summary = self.post_content.validate_clinical_summary(
            key_findings=list(cdha.get("key_findings") or []),
            impression=cdha.get("impression"),
            cdha_view_url=str(
                cdha.get("analysis_url") or job.data.get("cdha_view_url") or ""
            ),
        )
        combined = "\n".join(
            (
                str(job.data.get("clinical_factors") or ""),
                str(job.data.get("facebook_post_text") or ""),
                *summary.key_findings,
                summary.impression,
            )
        )
        privacy_scan = self.clinical_factors.privacy.scan(combined)
        self.repository.update_data(
            job_id,
            {
                "review_privacy_risk_level": privacy_scan.risk_level,
                "review_privacy_categories": list(privacy_scan.detected_categories),
                "review_media_pii_acknowledged": not automatic,
                "review_clinical_summary_validated_at": datetime.now(UTC).isoformat(),
                "review_clinical_summary": summary.to_dict(),
                "review_automatic": automatic,
            },
        )
        self.repository.transition(
            job_id,
            WorkflowStatus.APPROVED,
            details={
                "review_decision": (
                    "automatic_approval" if automatic else "approved_for_later_phase4"
                ),
                "privacy_risk_level": privacy_scan.risk_level,
                "media_pii_warning_acknowledged": not automatic,
            },
        )
        return ReviewDecision("approved")
        raise ValueError("Review selection must be a number from 1 to 7")

    def display(self, job_id: str) -> None:
        job = self.repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        data = job.data
        result = data.get("cdha_result") or {}
        caption = self.clinical_factors.privacy.mask(str(data.get("caption") or ""))
        comments = [
            self.clinical_factors.privacy.mask(
                self.clinical_factors._comment_content(item)
            )
            for item in list(data.get("comments") or [])[:10]
        ]
        factors = str(data.get("clinical_factors") or "")
        if not factors:
            factors = self._safe_read(data.get("clinical_factors_path"))
        review_text = "\n".join((caption, *comments, factors, str(data.get("facebook_post_text") or "")))
        privacy_scan = self.clinical_factors.privacy.scan(review_text)
        print("=" * 60)
        print("CDHA ANALYSIS READY FOR HUMAN REVIEW")
        print("=" * 60)
        print(f"Job ID: {job.job_id}")
        print(f"Source Reel: {job.source_url}")
        print(f"Caption (masked): {caption}")
        print(f"Comments used (masked, showing {len(comments)}):")
        for comment in comments:
            print(f"  - {comment}")
        print(f"Ollama input risk: {data.get('ai_input_risk_level') or data.get('gemini_input_risk_level') or 'LOW'}")
        print(f"Injection signals: {data.get('ai_input_suspicious_patterns') or data.get('gemini_input_suspicious_patterns') or []}")
        print(f"Clinical Factors path: {data.get('clinical_factors_path') or ''}")
        print("Clinical Factors (masked):")
        print(factors or "Không được cung cấp")
        print(f"CDHA view URL: {data.get('cdha_view_url') or ''}")
        print(f"CDHA result path: {data.get('cdha_result_json_path') or ''}")
        print(f"Triage: {result.get('triage') or ''}")
        print(f"Confidence: {result.get('confidence') or ''}")
        print(f"Key Findings: {result.get('key_findings') or []}")
        print(f"Impression: {result.get('impression') or ''}")
        print(f"Warnings: {result.get('warnings') or data.get('cdha_warnings') or []}")
        print("--- Ollama Diagnosis ---")
        print(f"AI Findings: {data.get('ai_findings') or []}")
        print(f"AI Impression: {data.get('ai_impression') or []}")
        print(f"AI Differential Diagnosis: {data.get('ai_differential_diagnosis') or []}")
        print("------------------------")
        print(f"Screenshots: {data.get('screenshot_paths') or []}")
        print(f"Final post text: {data.get('facebook_post_text') or '(generated after this review)'}")
        print(f"Content fingerprint: {data.get('facebook_content_hash') or '(computed before Facebook preparation)'}")
        print(f"Privacy scan: risk={privacy_scan.risk_level}, categories={list(privacy_scan.detected_categories)}")
        for warning in privacy_scan.warnings:
            print(f"PRIVACY WARNING: {warning}")
        print("\n[1] Approve for later Facebook publishing")
        print("[2] Reject")
        print("[3] Edit Clinical Factors")
        print("[4] Retry Ollama")
        print("[5] Retry CDHA")
        print("[6] Open screenshot folder")
        print("[7] Stop and resume later")

    @staticmethod
    def _safe_read(raw_path: object) -> str:
        if not raw_path:
            return ""
        try:
            path = Path(str(raw_path)).resolve()
            if path.is_file() and path.stat().st_size <= 100_000:
                return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass
        return ""

    def _save_edited_factors(self, job_id: str, edited: str) -> None:
        result = self.clinical_factors.validate(edited, job_id=job_id)
        if not result.success:
            raise ValueError(result.error or "Edited Clinical Factors failed validation")
        job_dir = (self.settings.job_data_dir / job_id).resolve()
        normalized_path = job_dir / "clinical-factors-normalized.txt"
        masked_path = job_dir / "clinical-factors-masked.txt"
        self._write_text_atomic(normalized_path, result.normalized_text)
        self._write_text_atomic(masked_path, result.masked_text)
        self.repository.update_data(
            job_id,
            {
                "clinical_factors": result.masked_text,
                "clinical_factors_normalized_path": str(normalized_path),
                "clinical_factors_path": str(masked_path),
                "clinical_factors_missing_fields": result.missing_fields,
                "clinical_factors_warnings": result.validation_warnings,
            },
        )

    @staticmethod
    def _read_multiline_edit() -> str:
        print("Paste the complete edited Clinical Factors. Enter a single '.' line to finish:")
        lines: list[str] = []
        while True:
            line = input()
            if line == ".":
                return "\n".join(lines)
            lines.append(line)

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        path.chmod(0o600)
