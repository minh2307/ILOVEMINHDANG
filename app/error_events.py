"""Safe structured payloads for job error and retry events."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.errors import PipelineError
from app.logging_setup import mask_sensitive
from app.services.privacy_service import PrivacyService


def safe_browser_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(str(value))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def safe_error_message(value: str) -> str:
    masked = mask_sensitive(str(value))
    masked = PrivacyService().mask(masked)
    return masked[:1000]


def build_error_event_details(
    error: PipelineError,
    *,
    attempt: int | None = None,
    browser_url: str | None = None,
    selector_key: str | None = None,
) -> dict[str, Any]:
    """Build an append-only event payload with no raw external content."""
    payload: dict[str, Any] = {
        "event_type": "operation_error",
        "error_code": error.error_code,
        "error_type": type(error).__name__,
        "message": safe_error_message(error.message),
        "retryable": bool(error.retryable),
        "manual_action_required": bool(error.manual_action_required),
        "phase": error.phase,
        "operation": error.operation,
        "attempt": attempt,
        "browser_url": safe_browser_url(browser_url),
        "selector_key": selector_key,
        "diagnostic_paths": list(error.diagnostic_paths),
    }
    return {key: value for key, value in payload.items() if value is not None}
