"""Authoritative workflow transition rules."""

from app.domain.exceptions.errors import InvalidTransitionError
from app.domain.enums.job_status import JobStatus


class JobStateTransitions:
    _transitions: dict[JobStatus, frozenset[JobStatus]] = {
        JobStatus.CREATED: frozenset({JobStatus.DOWNLOADREEL_RUNNING}),
        JobStatus.DOWNLOADREEL_RUNNING: frozenset({JobStatus.DOWNLOADED, JobStatus.DOWNLOADREEL_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.DOWNLOADED: frozenset({JobStatus.GEMINI_OPENING, JobStatus.AI_ANALYZING}),
        JobStatus.GEMINI_OPENING: frozenset({JobStatus.NEEDS_GEMINI_LOGIN, JobStatus.GEMINI_GENERATING, JobStatus.GEMINI_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.NEEDS_GEMINI_LOGIN: frozenset({JobStatus.GEMINI_GENERATING, JobStatus.GEMINI_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.GEMINI_GENERATING: frozenset({JobStatus.CLINICAL_FACTORS_GENERATED, JobStatus.GEMINI_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.AI_ANALYZING: frozenset({JobStatus.CLINICAL_FACTORS_GENERATED, JobStatus.AI_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.CLINICAL_FACTORS_GENERATED: frozenset({JobStatus.CDHA_OPENING}),
        JobStatus.CDHA_OPENING: frozenset({JobStatus.NEEDS_CDHA_LOGIN, JobStatus.CDHA_UPLOADING, JobStatus.CDHA_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.NEEDS_CDHA_LOGIN: frozenset({JobStatus.CDHA_UPLOADING, JobStatus.CDHA_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.CDHA_UPLOADING: frozenset({JobStatus.CDHA_ANALYZING, JobStatus.CDHA_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.CDHA_ANALYZING: frozenset({JobStatus.CDHA_ANALYZED, JobStatus.CDHA_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.CDHA_ANALYZED: frozenset({JobStatus.SCREENSHOTS_CAPTURING}),
        JobStatus.SCREENSHOTS_CAPTURING: frozenset({JobStatus.SCREENSHOTS_CAPTURED, JobStatus.CDHA_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.SCREENSHOTS_CAPTURED: frozenset({JobStatus.WAITING_FOR_REVIEW}),
        JobStatus.WAITING_FOR_REVIEW: frozenset({JobStatus.APPROVED, JobStatus.REJECTED, JobStatus.CDHA_OPENING, JobStatus.GEMINI_OPENING, JobStatus.AI_ANALYZING, JobStatus.RETRY_PENDING}),
        JobStatus.APPROVED: frozenset({JobStatus.FACEBOOK_PREPARING}),
        JobStatus.FACEBOOK_PREPARING: frozenset({JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW, JobStatus.FACEBOOK_PUBLISH_FAILED, JobStatus.WAITING_FOR_AUTH_REVIEW, JobStatus.RETRYABLE, JobStatus.RETRY_PENDING, JobStatus.BLOCKED}),
        JobStatus.WAITING_FOR_AUTH_REVIEW: frozenset({JobStatus.FACEBOOK_PREPARING, JobStatus.RETRYABLE, JobStatus.BLOCKED}),
        JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW: frozenset({JobStatus.FACEBOOK_PUBLISHING, JobStatus.FACEBOOK_PUBLISH_FAILED, JobStatus.APPROVED}),
        JobStatus.FACEBOOK_PUBLISHING: frozenset({JobStatus.FACEBOOK_PUBLISHED, JobStatus.FACEBOOK_PUBLISH_FAILED, JobStatus.FACEBOOK_PUBLISH_UNCERTAIN, JobStatus.PUBLISH_RECONCILIATION_REQUIRED, JobStatus.AUTHENTICATION_REQUIRED}),
        JobStatus.FACEBOOK_PUBLISHED: frozenset({JobStatus.POST_URL_EXTRACTING}),
        JobStatus.POST_URL_EXTRACTING: frozenset({JobStatus.POST_URL_EXTRACTED, JobStatus.POST_URL_EXTRACTION_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.POST_URL_EXTRACTED: frozenset({JobStatus.COMMENT_ADDING}),
        JobStatus.COMMENT_ADDING: frozenset({JobStatus.COMMENT_ADDED, JobStatus.COMMENT_FAILED, JobStatus.RETRY_PENDING}),
        JobStatus.COMMENT_ADDED: frozenset({JobStatus.COMPLETED}),
        JobStatus.DOWNLOADREEL_FAILED: frozenset({JobStatus.RETRY_PENDING}),
        JobStatus.GEMINI_FAILED: frozenset({JobStatus.RETRY_PENDING}),
        JobStatus.AI_FAILED: frozenset({JobStatus.RETRY_PENDING}),
        JobStatus.CDHA_FAILED: frozenset({JobStatus.RETRY_PENDING}),
        JobStatus.FACEBOOK_PUBLISH_FAILED: frozenset({JobStatus.RETRY_PENDING}),
        JobStatus.POST_URL_EXTRACTION_FAILED: frozenset({JobStatus.RETRY_PENDING}),
        JobStatus.COMMENT_FAILED: frozenset({JobStatus.RETRY_PENDING}),
        # FACEBOOK_PUBLISH_UNCERTAIN: can escalate to formal reconciliation required,
        # or loop back to itself during incremental reconcile attempts.
        JobStatus.FACEBOOK_PUBLISH_UNCERTAIN: frozenset({
            JobStatus.PUBLISH_RECONCILIATION_REQUIRED,
            JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
        }),
        # PUBLISH_RECONCILIATION_REQUIRED: can resolve to PUBLISHED (post found)
        # or fall back to UNCERTAIN (post not found, try again later), or FAILED.
        JobStatus.PUBLISH_RECONCILIATION_REQUIRED: frozenset({
            JobStatus.FACEBOOK_PUBLISHED,
            JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
            JobStatus.FACEBOOK_PUBLISH_FAILED,
        }),
        JobStatus.AUTHENTICATION_REQUIRED: frozenset({JobStatus.FACEBOOK_PREPARING, JobStatus.FACEBOOK_PUBLISHING, JobStatus.WAITING_FOR_AUTH_REVIEW}),
        JobStatus.RETRY_PENDING: frozenset({JobStatus.DOWNLOADREEL_RUNNING, JobStatus.GEMINI_OPENING, JobStatus.AI_ANALYZING, JobStatus.CDHA_OPENING, JobStatus.SCREENSHOTS_CAPTURING, JobStatus.FACEBOOK_PREPARING, JobStatus.POST_URL_EXTRACTING, JobStatus.COMMENT_ADDING}),
        JobStatus.RETRYABLE: frozenset({JobStatus.DOWNLOADREEL_RUNNING, JobStatus.AI_ANALYZING, JobStatus.CDHA_OPENING, JobStatus.FACEBOOK_PREPARING, JobStatus.POST_URL_EXTRACTING, JobStatus.COMMENT_ADDING}),
        JobStatus.BLOCKED: frozenset({JobStatus.RETRYABLE}),
        JobStatus.REJECTED: frozenset(),
        JobStatus.COMPLETED: frozenset(),
        JobStatus.FAILED: frozenset(),
        JobStatus.CANCELLED: frozenset(),
        JobStatus.ACQUIRING_BROWSER_LOCK: frozenset(),
        JobStatus.WAITING_FOR_BROWSER_LOCK: frozenset(),
        JobStatus.RUNNING: frozenset(),
    }

    @classmethod
    def allowed_targets(cls, current: JobStatus) -> frozenset[JobStatus]:
        targets = cls._transitions.get(current, frozenset())
        terminal = {JobStatus.COMPLETED, JobStatus.REJECTED, JobStatus.FAILED, JobStatus.CANCELLED}
        return targets if current in terminal else targets | {JobStatus.FAILED, JobStatus.CANCELLED}

    @classmethod
    def validate(
        cls,
        current: JobStatus,
        target: JobStatus,
        *,
        job_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        if target not in cls.allowed_targets(current):
            context = [
                f"job_id={job_id or 'unknown'}",
                f"old_state={current.value}",
                f"requested_state={target.value}",
                f"reason={reason or 'not provided'}",
            ]
            raise InvalidTransitionError(
                "Invalid workflow transition: " + ", ".join(context)
            )


WorkflowStateMachine = JobStateTransitions
