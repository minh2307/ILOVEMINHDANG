from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class WorkflowStatus(StrEnum):
    CREATED = "CREATED"
    DOWNLOADREEL_RUNNING = "DOWNLOADREEL_RUNNING"
    DOWNLOADED = "DOWNLOADED"
    DOWNLOADREEL_FAILED = "DOWNLOADREEL_FAILED"
    GEMINI_OPENING = "GEMINI_OPENING"
    NEEDS_GEMINI_LOGIN = "NEEDS_GEMINI_LOGIN"
    GEMINI_GENERATING = "GEMINI_GENERATING"
    CLINICAL_FACTORS_GENERATED = "CLINICAL_FACTORS_GENERATED"
    GEMINI_FAILED = "GEMINI_FAILED"
    AI_ANALYZING = "AI_ANALYZING"
    AI_FAILED = "AI_FAILED"
    CDHA_OPENING = "CDHA_OPENING"
    NEEDS_CDHA_LOGIN = "NEEDS_CDHA_LOGIN"
    CDHA_UPLOADING = "CDHA_UPLOADING"
    CDHA_ANALYZING = "CDHA_ANALYZING"
    CDHA_ANALYZED = "CDHA_ANALYZED"
    CDHA_FAILED = "CDHA_FAILED"
    SCREENSHOTS_CAPTURING = "SCREENSHOTS_CAPTURING"
    SCREENSHOTS_CAPTURED = "SCREENSHOTS_CAPTURED"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FACEBOOK_PREPARING = "FACEBOOK_PREPARING"
    FACEBOOK_WAITING_FOR_MANUAL_REVIEW = "FACEBOOK_WAITING_FOR_MANUAL_REVIEW"
    FACEBOOK_PUBLISHING = "FACEBOOK_PUBLISHING"
    FACEBOOK_PUBLISHED = "FACEBOOK_PUBLISHED"
    FACEBOOK_PUBLISH_FAILED = "FACEBOOK_PUBLISH_FAILED"
    FACEBOOK_PUBLISH_UNCERTAIN = "FACEBOOK_PUBLISH_UNCERTAIN"
    POST_URL_EXTRACTING = "POST_URL_EXTRACTING"
    POST_URL_EXTRACTED = "POST_URL_EXTRACTED"
    POST_URL_EXTRACTION_FAILED = "POST_URL_EXTRACTION_FAILED"
    COMMENT_ADDING = "COMMENT_ADDING"
    COMMENT_ADDED = "COMMENT_ADDED"
    COMMENT_FAILED = "COMMENT_FAILED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class JobRecord:
    job_id: str
    source_url: str
    status: WorkflowStatus
    normalized_source_url: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_url": self.source_url,
            "normalized_source_url": self.normalized_source_url,
            "status": self.status.value,
            "data": self.data,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class JobEvent:
    event_id: int
    job_id: str
    from_status: WorkflowStatus | None
    to_status: WorkflowStatus
    details: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "job_id": self.job_id,
            "from_status": self.from_status.value if self.from_status else None,
            "to_status": self.to_status.value,
            "details": self.details,
            "created_at": self.created_at,
        }
