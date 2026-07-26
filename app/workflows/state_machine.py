from __future__ import annotations

from app.errors import InvalidTransitionError
from app.models.workflow import WorkflowStatus


class WorkflowStateMachine:
    _transitions: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
        WorkflowStatus.CREATED: frozenset({WorkflowStatus.DOWNLOADREEL_RUNNING}),
        WorkflowStatus.DOWNLOADREEL_RUNNING: frozenset(
            {WorkflowStatus.DOWNLOADED, WorkflowStatus.DOWNLOADREEL_FAILED}
        ),
        WorkflowStatus.DOWNLOADED: frozenset({WorkflowStatus.GEMINI_OPENING}),
        WorkflowStatus.GEMINI_OPENING: frozenset(
            {
                WorkflowStatus.NEEDS_GEMINI_LOGIN,
                WorkflowStatus.GEMINI_GENERATING,
                WorkflowStatus.GEMINI_FAILED,
            }
        ),
        WorkflowStatus.NEEDS_GEMINI_LOGIN: frozenset(
            {WorkflowStatus.GEMINI_GENERATING, WorkflowStatus.GEMINI_FAILED, WorkflowStatus.RETRY_PENDING}
        ),
        WorkflowStatus.GEMINI_GENERATING: frozenset(
            {WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.GEMINI_FAILED}
        ),
        WorkflowStatus.CLINICAL_FACTORS_GENERATED: frozenset({WorkflowStatus.CDHA_OPENING}),
        WorkflowStatus.CDHA_OPENING: frozenset(
            {
                WorkflowStatus.NEEDS_CDHA_LOGIN,
                WorkflowStatus.CDHA_UPLOADING,
                WorkflowStatus.CDHA_FAILED,
            }
        ),
        WorkflowStatus.NEEDS_CDHA_LOGIN: frozenset(
            {WorkflowStatus.CDHA_UPLOADING, WorkflowStatus.CDHA_FAILED, WorkflowStatus.RETRY_PENDING}
        ),
        WorkflowStatus.CDHA_UPLOADING: frozenset(
            {WorkflowStatus.CDHA_ANALYZING, WorkflowStatus.CDHA_FAILED}
        ),
        WorkflowStatus.CDHA_ANALYZING: frozenset(
            {WorkflowStatus.CDHA_ANALYZED, WorkflowStatus.CDHA_FAILED}
        ),
        WorkflowStatus.CDHA_ANALYZED: frozenset({WorkflowStatus.SCREENSHOTS_CAPTURING}),
        WorkflowStatus.SCREENSHOTS_CAPTURING: frozenset(
            {WorkflowStatus.SCREENSHOTS_CAPTURED, WorkflowStatus.CDHA_FAILED}
        ),
        WorkflowStatus.SCREENSHOTS_CAPTURED: frozenset({WorkflowStatus.WAITING_FOR_REVIEW}),
        WorkflowStatus.WAITING_FOR_REVIEW: frozenset(
            {
                WorkflowStatus.APPROVED,
                WorkflowStatus.REJECTED,
                WorkflowStatus.CDHA_OPENING,
                WorkflowStatus.GEMINI_OPENING,
                WorkflowStatus.RETRY_PENDING,
            }
        ),
        WorkflowStatus.APPROVED: frozenset({WorkflowStatus.FACEBOOK_PREPARING}),
        WorkflowStatus.FACEBOOK_PREPARING: frozenset(
            {
                WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
                WorkflowStatus.FACEBOOK_PUBLISH_FAILED,
            }
        ),
        WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW: frozenset(
            {
                WorkflowStatus.FACEBOOK_PUBLISHING,
                WorkflowStatus.FACEBOOK_PUBLISH_FAILED,
                WorkflowStatus.APPROVED,
            }
        ),
        WorkflowStatus.FACEBOOK_PUBLISHING: frozenset(
            {
                WorkflowStatus.FACEBOOK_PUBLISHED,
                WorkflowStatus.FACEBOOK_PUBLISH_FAILED,
                WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
            }
        ),
        WorkflowStatus.FACEBOOK_PUBLISHED: frozenset({WorkflowStatus.POST_URL_EXTRACTING}),
        WorkflowStatus.POST_URL_EXTRACTING: frozenset(
            {WorkflowStatus.POST_URL_EXTRACTED, WorkflowStatus.POST_URL_EXTRACTION_FAILED}
        ),
        WorkflowStatus.POST_URL_EXTRACTED: frozenset({WorkflowStatus.COMMENT_ADDING}),
        WorkflowStatus.COMMENT_ADDING: frozenset(
            {WorkflowStatus.COMMENT_ADDED, WorkflowStatus.COMMENT_FAILED}
        ),
        WorkflowStatus.COMMENT_ADDED: frozenset({WorkflowStatus.COMPLETED}),
        WorkflowStatus.DOWNLOADREEL_FAILED: frozenset({WorkflowStatus.RETRY_PENDING}),
        WorkflowStatus.GEMINI_FAILED: frozenset({WorkflowStatus.RETRY_PENDING}),
        WorkflowStatus.CDHA_FAILED: frozenset({WorkflowStatus.RETRY_PENDING}),
        WorkflowStatus.FACEBOOK_PUBLISH_FAILED: frozenset({WorkflowStatus.RETRY_PENDING}),
        WorkflowStatus.POST_URL_EXTRACTION_FAILED: frozenset({WorkflowStatus.RETRY_PENDING}),
        WorkflowStatus.COMMENT_FAILED: frozenset({WorkflowStatus.RETRY_PENDING}),
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN: frozenset(),
        WorkflowStatus.RETRY_PENDING: frozenset(
            {
                WorkflowStatus.DOWNLOADREEL_RUNNING,
                WorkflowStatus.GEMINI_OPENING,
                WorkflowStatus.CDHA_OPENING,
                WorkflowStatus.FACEBOOK_PREPARING,
                WorkflowStatus.POST_URL_EXTRACTING,
                WorkflowStatus.COMMENT_ADDING,
            }
        ),
        WorkflowStatus.REJECTED: frozenset(),
        WorkflowStatus.COMPLETED: frozenset(),
        WorkflowStatus.FAILED: frozenset(),
        WorkflowStatus.CANCELLED: frozenset(),
    }

    @classmethod
    def allowed_targets(cls, current: WorkflowStatus) -> frozenset[WorkflowStatus]:
        targets = cls._transitions.get(current, frozenset())
        terminal = {WorkflowStatus.COMPLETED, WorkflowStatus.REJECTED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
        if current not in terminal:
            return targets | {WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
        return targets

    @classmethod
    def validate(cls, current: WorkflowStatus, target: WorkflowStatus) -> None:
        if target not in cls.allowed_targets(current):
            raise InvalidTransitionError(f"Invalid workflow transition: {current.value} -> {target.value}")
