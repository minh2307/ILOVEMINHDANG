"""Canonical domain error taxonomy for the CDHA automation pipeline."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class PipelineError(RuntimeError):
    """Base error carrying safe operational metadata.

    Subclasses define stable class-level defaults. Callers may override the
    code when a single class represents multiple service-specific operations.
    """

    error_code = "PIPELINE_ERROR"
    retryable = False
    manual_action_required = False

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        retryable: bool | None = None,
        manual_action_required: bool | None = None,
        phase: str | None = None,
        operation: str | None = None,
        job_id: str | None = None,
        diagnostic_paths: tuple[str, ...] = (),
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.error_code = error_code or type(self).error_code
        self.retryable = type(self).retryable if retryable is None else retryable
        self.manual_action_required = (
            type(self).manual_action_required
            if manual_action_required is None
            else manual_action_required
        )
        self.phase = phase
        self.operation = operation
        self.job_id = job_id
        self.diagnostic_paths = tuple(diagnostic_paths)
        self.details = dict(details or {})


# Backwards-compatible name used by the existing Phase 5 result module.
CDHAPipelineError = PipelineError


class ConfigurationError(PipelineError):
    error_code = "CONFIGURATION_ERROR"


class ValidationError(PipelineError):
    error_code = "VALIDATION_ERROR"


class BrowserAutomationError(PipelineError):
    error_code = "BROWSER_AUTOMATION_ERROR"


class BrowserTimeoutError(BrowserAutomationError):
    error_code = "BROWSER_TIMEOUT"
    retryable = True


class BrowserTargetClosedError(BrowserAutomationError):
    error_code = "BROWSER_TARGET_CLOSED"
    retryable = True


class BrowserDisconnectedError(BrowserTargetClosedError):
    error_code = "BROWSER_DISCONNECTED"
    retryable = True


class BrowserContextClosedError(BrowserTargetClosedError):
    error_code = "BROWSER_CONTEXT_CLOSED"
    retryable = True


class BrowserPageClosedError(BrowserTargetClosedError):
    error_code = "BROWSER_PAGE_CLOSED"
    retryable = True


class BrowserPageOwnershipError(BrowserAutomationError):
    error_code = "BROWSER_PAGE_NOT_OWNED"


class BrowserNetworkError(BrowserAutomationError):
    error_code = "BROWSER_NETWORK_ERROR"
    retryable = True


class SelectorNotFoundError(BrowserAutomationError):
    error_code = "SELECTOR_NOT_FOUND"
    manual_action_required = True


class FrameNotReadyError(BrowserAutomationError):
    error_code = "FRAME_NOT_READY"
    retryable = True


class AuthenticationRequiredError(BrowserAutomationError):
    error_code = "AUTHENTICATION_REQUIRED"
    manual_action_required = True


class CheckpointRequiredError(AuthenticationRequiredError):
    error_code = "CHECKPOINT_REQUIRED"


class CDHAUploadError(PipelineError):
    error_code = "CDHA_UPLOAD_ERROR"
    retryable = True


class CDHARenderError(PipelineError):
    error_code = "CDHA_RENDER_ERROR"
    retryable = True


class CDHAAuthenticationRequiredError(AuthenticationRequiredError):
    error_code = "CDHA_AUTHENTICATION_REQUIRED"


class CDHASelectorMismatchError(SelectorNotFoundError):
    error_code = "CDHA_SELECTOR_MISMATCH"


class CDHAControlHiddenError(CDHARenderError):
    error_code = "CDHA_CONTROL_HIDDEN"
    retryable = True


class CDHAControlDisabledError(CDHARenderError):
    error_code = "CDHA_CONTROL_DISABLED"
    retryable = True


class CDHAAnalysisTimeoutError(BrowserTimeoutError, TimeoutError):
    error_code = "CDHA_ANALYSIS_TIMEOUT"


class QueueLeaseExpiredError(PipelineError):
    error_code = "QUEUE_LEASE_EXPIRED"
    retryable = True


class GeminiAnalysisError(PipelineError):
    error_code = "GEMINI_ANALYSIS_ERROR"
    retryable = True


class FacebookPublicationError(PipelineError):
    error_code = "FACEBOOK_PUBLICATION_ERROR"
    manual_action_required = True


class FacebookPublicationUncertainError(FacebookPublicationError):
    error_code = "FACEBOOK_PUBLICATION_UNCERTAIN"


class FacebookVerificationError(FacebookPublicationError):
    error_code = "FACEBOOK_VERIFICATION_ERROR"


class PromptSafetyError(PipelineError):
    error_code = "PROMPT_SAFETY_ERROR"
    manual_action_required = True


class PrivacyValidationError(PipelineError):
    error_code = "PRIVACY_VALIDATION_ERROR"
    manual_action_required = True


class InvalidTransitionError(ValidationError, ValueError):
    error_code = "INVALID_STATE_TRANSITION"


class RepositoryError(PipelineError):
    error_code = "REPOSITORY_ERROR"
    retryable = True


# Legacy names retained as subclasses/aliases of the canonical hierarchy.
class ProfileLockError(BrowserAutomationError):
    error_code = "BROWSER_PROFILE_LOCKED"
    retryable = True
    manual_action_required = True


LoginRequiredError = AuthenticationRequiredError


class ManualActionRequiredError(PipelineError):
    error_code = "MANUAL_ACTION_REQUIRED"
    manual_action_required = True


class DownloadError(PipelineError):
    error_code = "DOWNLOAD_ERROR"
    retryable = True


GeminiError = GeminiAnalysisError


class ClinicalFactorsValidationError(ValidationError):
    error_code = "CLINICAL_FACTORS_VALIDATION_ERROR"


CDHAAnalysisError = CDHARenderError


class CDHATimeoutError(BrowserTimeoutError):
    error_code = "CDHA_TIMEOUT"


class ScreenshotError(BrowserAutomationError):
    error_code = "SCREENSHOT_ERROR"
    retryable = True


class ReviewRequiredError(PipelineError):
    error_code = "REVIEW_REQUIRED"
    manual_action_required = True


class FacebookPreparationError(BrowserAutomationError):
    error_code = "FACEBOOK_PREPARATION_ERROR"
    retryable = True


FacebookPublishError = FacebookPublicationError
FacebookPublishUncertainError = FacebookPublicationUncertainError


class PermalinkExtractionError(BrowserAutomationError):
    error_code = "FACEBOOK_PERMALINK_ERROR"
    retryable = True


class FacebookCommentError(PipelineError):
    error_code = "FACEBOOK_COMMENT_ERROR"




class RetryExhaustedError(PipelineError):
    error_code = "RETRY_EXHAUSTED"
    manual_action_required = True


class OperatorCancelledError(PipelineError):
    error_code = "OPERATOR_CANCELLED"


PersistenceError = RepositoryError
