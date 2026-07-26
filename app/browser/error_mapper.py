"""Map public Playwright failures to stable pipeline domain errors."""
from __future__ import annotations

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.errors import (
    AuthenticationRequiredError,
    BrowserAutomationError,
    BrowserNetworkError,
    BrowserTargetClosedError,
    BrowserTimeoutError,
    CheckpointRequiredError,
    FrameNotReadyError,
    PipelineError,
    SelectorNotFoundError,
)

_TARGET_CLOSED_MARKERS = (
    "target page, context or browser has been closed",
    "target closed",
    "page has been closed",
    "page closed",
    "context has been closed",
    "browser has been closed",
    "browser closed",
)
_FRAME_MARKERS = (
    "frame was detached",
    "frame has been detached",
    "frame got detached",
    "detached frame",
    "execution context was destroyed",
)
_NETWORK_MARKERS = (
    "net::err_",
    "network error",
    "connection reset",
    "connection refused",
    "connection closed",
    "name not resolved",
    "temporarily unavailable",
    "navigation failed",
)
_SELECTOR_SYNTAX_MARKERS = (
    "unexpected token",
    "invalid selector",
    "failed to parse selector",
    "unknown engine",
)
_CHECKPOINT_MARKERS = ("checkpoint", "captcha", "two-factor", "two factor", "2fa")
_AUTH_MARKERS = ("authentication required", "login required", "sign in required")


def map_playwright_error(
    exc: Exception,
    *,
    phase: str,
    operation: str,
    job_id: str | None = None,
) -> PipelineError:
    """Return a typed error without importing Playwright private classes."""
    if isinstance(exc, PipelineError):
        return exc

    message = str(exc) or type(exc).__name__
    lowered = message.casefold()
    metadata = {"phase": phase, "operation": operation, "job_id": job_id}

    if any(marker in lowered for marker in _CHECKPOINT_MARKERS):
        return CheckpointRequiredError(message, **metadata)
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return AuthenticationRequiredError(message, **metadata)
    if isinstance(exc, PlaywrightTimeoutError):
        return BrowserTimeoutError(message, **metadata)
    if any(marker in lowered for marker in _TARGET_CLOSED_MARKERS):
        return BrowserTargetClosedError(message, **metadata)
    if any(marker in lowered for marker in _FRAME_MARKERS):
        return FrameNotReadyError(message, **metadata)
    if any(marker in lowered for marker in _NETWORK_MARKERS):
        return BrowserNetworkError(message, **metadata)
    if type(exc).__name__ == "SelectorResolutionError":
        return SelectorNotFoundError(message, **metadata)
    if any(marker in lowered for marker in _SELECTOR_SYNTAX_MARKERS):
        return SelectorNotFoundError(
            message,
            error_code="SELECTOR_SYNTAX_ERROR",
            manual_action_required=True,
            **metadata,
        )
    if isinstance(exc, PlaywrightError):
        return BrowserAutomationError(message, **metadata)
    return BrowserAutomationError(message, **metadata)


def is_terminal_browser_condition(error: PipelineError) -> bool:
    """Whether selector fallback cannot safely recover on the same target."""
    return isinstance(
        error,
        (BrowserTargetClosedError, FrameNotReadyError, BrowserNetworkError),
    ) or error.error_code == "SELECTOR_SYNTAX_ERROR"
