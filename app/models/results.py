from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DownloadComment:
    author: str | None
    content: str
    published_at: None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "author": self.author,
            "content": self.content,
            "published_at": self.published_at,
        }


NormalizedComment = DownloadComment


@dataclass(frozen=True, slots=True)
class DownloadResult:
    job_id: str
    source_url: str
    normalized_source_url: str
    video_path: Path | None = None
    video_filename: str = ""
    video_size_bytes: int = 0
    caption: str = ""
    comments: list[DownloadComment] = field(default_factory=list)
    metadata_path: Path | None = None
    downloaded_at: str = ""
    checksum_sha256: str = ""
    success: bool = False
    error: str | None = None
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_url": self.source_url,
            "normalized_source_url": self.normalized_source_url,
            "video_path": str(self.video_path) if self.video_path else None,
            "video_filename": self.video_filename,
            "video_size_bytes": self.video_size_bytes,
            "caption": self.caption,
            "comments": [comment.to_dict() for comment in self.comments],
            "metadata_path": str(self.metadata_path) if self.metadata_path else None,
            "downloaded_at": self.downloaded_at,
            "checksum_sha256": self.checksum_sha256,
            "success": self.success,
            "error": self.error,
            "reused": self.reused,
        }


@dataclass(frozen=True, slots=True)
class ClinicalFactorsResult:
    success: bool
    job_id: str
    raw_response: str = ""
    normalized_text: str = ""
    masked_text: str = ""
    missing_fields: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    raw_response_path: str | Path | None = None
    normalized_output_path: str | Path | None = None
    generated_at: datetime | str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "job_id": self.job_id,
            "raw_response": self.raw_response,
            "normalized_text": self.normalized_text,
            "masked_text": self.masked_text,
            "missing_fields": list(self.missing_fields),
            "validation_warnings": list(self.validation_warnings),
            "raw_response_path": _serialize_path(self.raw_response_path),
            "normalized_output_path": _serialize_path(self.normalized_output_path),
            "generated_at": _serialize_datetime(self.generated_at),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class CDHAAnalysisResult:
    success: bool
    job_id: str
    triage: str | None = None
    confidence: str | None = None
    key_findings: list[str] = field(default_factory=list)
    impression: str | None = None
    analysis_url: str = ""
    source_language: str | None = None
    raw_key_findings: str | None = None
    raw_impression: str | None = None
    detailed_analysis: str | None = None
    marked_regions: list[str] = field(default_factory=list)
    raw_text: str = ""
    result_json_path: str | Path | None = None
    result_html_path: str | Path | None = None
    diagnostic_screenshot_path: str | Path | None = None
    screenshot_paths: list[str | Path] = field(default_factory=list)
    started_at: datetime | str | None = None
    completed_at: datetime | str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "job_id": self.job_id,
            "triage": self.triage,
            "confidence": self.confidence,
            "key_findings": list(self.key_findings),
            "impression": self.impression,
            "analysis_url": self.analysis_url,
            "source_language": self.source_language,
            "raw_key_findings": self.raw_key_findings,
            "raw_impression": self.raw_impression,
            "detailed_analysis": self.detailed_analysis,
            "marked_regions": list(self.marked_regions),
            "raw_text": self.raw_text,
            "result_json_path": _serialize_path(self.result_json_path),
            "result_html_path": _serialize_path(self.result_html_path),
            "diagnostic_screenshot_path": _serialize_path(self.diagnostic_screenshot_path),
            "screenshot_paths": [str(path) for path in self.screenshot_paths],
            "started_at": _serialize_datetime(self.started_at),
            "completed_at": _serialize_datetime(self.completed_at),
            "warnings": list(self.warnings),
            "error": self.error,
        }


def _serialize_path(value: str | Path | None) -> str | None:
    return str(value) if value is not None else None


def _serialize_datetime(value: datetime | str | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


@dataclass(frozen=True, slots=True)
class FacebookPostValidationResult:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    content_fingerprint: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "content_fingerprint": self.content_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class FacebookPostPreparationResult:
    success: bool
    job_id: str
    target_url: str = ""
    post_text: str = ""
    image_paths: list[str] = field(default_factory=list)
    uploaded_image_count: int = 0
    expected_image_count: int = 0
    preview_screenshot_path: str | Path | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success, "job_id": self.job_id,
            "target_url": self.target_url, "post_text": self.post_text,
            "image_paths": [str(path) for path in self.image_paths],
            "uploaded_image_count": self.uploaded_image_count,
            "expected_image_count": self.expected_image_count,
            "preview_screenshot_path": _serialize_path(self.preview_screenshot_path),
            "warnings": list(self.warnings), "error": self.error,
        }


@dataclass(frozen=True)
class FacebookPublishResult:
    """Authoritative result for a Facebook publication attempt.

    Required fields:
        success: bool   — True only when a verified post ID or permalink was obtained.
        status: str     — One of the PUBLICATION_STATUS_* constants or a job_id
                          (legacy callers pass job_id here; new callers pass status string).
        target_url: str — Configured target Facebook Page URL.

    All remaining fields have defaults and should be populated when available.
    """

    # --- Core required fields ---
    success: bool
    status: str       # publication status string (e.g. PUBLISHED_VERIFIED) or job_id for legacy
    target_url: str = ""

    # --- Verification evidence (required for PUBLISHED_VERIFIED) ---
    post_id: str | None = None
    permalink: str | None = None
    published_at: datetime | str | None = None
    verification_method: str | None = None

    # --- Fingerprint & attempt tracking ---
    content_fingerprint: str = ""
    attempt_id: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    artifact_paths: tuple[str, ...] = ()

    # --- Legacy compatibility fields ---
    job_id: str = ""
    post_url: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    # --- Extra diagnostic fields stored but not required ---
    diagnostic_screenshot_path: str | None = None

    @property
    def is_verified(self) -> bool:
        """True only when durable post evidence is present."""
        return self.success and bool(self.post_id or self.permalink)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success, "status": self.status,
            "target_url": self.target_url, "post_id": self.post_id,
            "permalink": self.permalink,
            "published_at": _serialize_datetime(self.published_at),
            "verification_method": self.verification_method,
            "content_fingerprint": self.content_fingerprint,
            "attempt_id": self.attempt_id,
            "diagnostics": dict(self.diagnostics),
            "artifact_paths": list(self.artifact_paths),
            "job_id": self.job_id,
            "post_url": self.post_url or self.permalink,
            "warnings": list(self.warnings), "error": self.error,
            "is_verified": self.is_verified,
        }


@dataclass(frozen=True, slots=True)
class FacebookPermalinkResult:
    success: bool
    job_id: str
    post_url: str | None = None
    post_id: str | None = None
    extraction_method: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success, "job_id": self.job_id,
            "post_url": self.post_url, "post_id": self.post_id,
            "extraction_method": self.extraction_method,
            "warnings": list(self.warnings), "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class FacebookCommentResult:
    success: bool
    job_id: str
    post_url: str = ""
    comment_id: str | None = None
    comment_text: str = ""
    posted_at: datetime | str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success, "job_id": self.job_id,
            "post_url": self.post_url, "comment_id": self.comment_id,
            "comment_text": self.comment_text,
            "posted_at": _serialize_datetime(self.posted_at),
            "warnings": list(self.warnings), "error": self.error,
            "reused": self.reused,
        }


@dataclass(frozen=True, slots=True)
class FacebookWorkflowResult:
    success: bool
    job_id: str
    status: str
    post_url: str | None = None
    comment_reused: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success, "job_id": self.job_id, "status": self.status,
            "post_url": self.post_url, "comment_reused": self.comment_reused,
            "warnings": list(self.warnings), "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Typed result for the end-to-end Phase 5 pipeline orchestrator."""

    success: bool
    job_id: str
    current_status: str
    source_url: str = ""
    video_path: str | None = None
    clinical_factors_path: str | None = None
    cdha_result_path: str | None = None
    screenshot_paths: list[str] = field(default_factory=list)
    facebook_post_url: str | None = None
    facebook_comment_id: str | None = None
    completed_steps: list[str] = field(default_factory=list)
    pending_manual_action: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    completed_at: datetime | str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "job_id": self.job_id,
            "current_status": self.current_status,
            "source_url": self.source_url,
            "video_path": self.video_path,
            "clinical_factors_path": self.clinical_factors_path,
            "cdha_result_path": self.cdha_result_path,
            "screenshot_paths": list(self.screenshot_paths),
            "facebook_post_url": self.facebook_post_url,
            "facebook_comment_id": self.facebook_comment_id,
            "completed_steps": list(self.completed_steps),
            "pending_manual_action": self.pending_manual_action,
            "warnings": list(self.warnings),
            "error": self.error,
            "started_at": _serialize_datetime(self.started_at),
            "updated_at": _serialize_datetime(self.updated_at),
            "completed_at": _serialize_datetime(self.completed_at),
        }


# Canonical error hierarchy lives in app.errors. Re-export legacy names here
# so existing imports continue to work without a competing implementation.
from app.errors import (
    CDHAAnalysisError,
    CDHAPipelineError,
    CDHATimeoutError,
    CDHAUploadError,
    ClinicalFactorsValidationError,
    ConfigurationError,
    DownloadError,
    FacebookCommentError,
    FacebookPreparationError,
    FacebookPublishError,
    FacebookPublishUncertainError,
    GeminiError,
    LoginRequiredError,
    ManualActionRequiredError,
    OperatorCancelledError,
    PermalinkExtractionError,
    PersistenceError,
    ProfileLockError,
    RetryExhaustedError,
    ReviewRequiredError,
    ScreenshotError,
    ValidationError,
)
