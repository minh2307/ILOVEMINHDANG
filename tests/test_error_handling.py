from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.browser.error_mapper import map_playwright_error
from app.browser.selector_resolver import SelectorResolver
from app.error_events import build_error_event_details
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
from app.repositories.job_repository import JobRepository


def test_pipeline_error_metadata_and_string_contract() -> None:
    error = BrowserTimeoutError(
        "wait expired", phase="CDHA_UPLOADING", operation="resolve_frame", job_id="j"
    )
    assert str(error) == "wait expired"
    assert error.error_code == "BROWSER_TIMEOUT"
    assert error.retryable is True
    assert error.manual_action_required is False
    assert error.phase == "CDHA_UPLOADING"


def test_playwright_error_mapping_uses_public_types_and_stable_signals() -> None:
    timeout = map_playwright_error(
        PlaywrightTimeoutError("Timeout 1000ms exceeded"),
        phase="CDHA",
        operation="wait_result",
    )
    closed = map_playwright_error(
        PlaywrightError("Target page, context or browser has been closed"),
        phase="CDHA",
        operation="resolve_frame",
    )
    frame = map_playwright_error(
        PlaywrightError("Frame was detached"), phase="CDHA", operation="upload"
    )
    network = map_playwright_error(
        PlaywrightError("page.goto: net::ERR_CONNECTION_RESET"),
        phase="GEMINI",
        operation="navigate",
    )
    checkpoint = map_playwright_error(
        PlaywrightError("Facebook security checkpoint"),
        phase="FACEBOOK",
        operation="authenticate",
    )
    authentication = map_playwright_error(
        PlaywrightError("Login required"), phase="GEMINI", operation="authenticate"
    )
    page_closed = map_playwright_error(
        PlaywrightError("Page has been closed"), phase="CDHA", operation="poll"
    )
    syntax = map_playwright_error(
        PlaywrightError("Failed to parse selector"),
        phase="FACEBOOK",
        operation="resolve_selector",
    )
    unknown = map_playwright_error(
        RuntimeError("unexpected"), phase="CDHA", operation="unknown"
    )

    assert isinstance(timeout, BrowserTimeoutError)
    assert isinstance(closed, BrowserTargetClosedError)
    assert isinstance(frame, FrameNotReadyError)
    assert isinstance(network, BrowserNetworkError)
    assert isinstance(checkpoint, CheckpointRequiredError)
    assert isinstance(authentication, AuthenticationRequiredError)
    assert authentication.manual_action_required is True
    assert isinstance(page_closed, BrowserTargetClosedError)
    assert isinstance(syntax, SelectorNotFoundError)
    assert syntax.retryable is False
    assert isinstance(unknown, BrowserAutomationError)
    assert unknown.retryable is False


class _FailingLocator:
    @property
    def first(self):
        return self

    async def wait_for(self, **_):
        raise PlaywrightError("Target page, context or browser has been closed")


class _ClosedPage:
    url = "https://example.invalid"

    def locator(self, _selector):
        return _FailingLocator()


def test_selector_resolver_does_not_hide_closed_target(tmp_path: Path) -> None:
    config = tmp_path / "selectors.yaml"
    config.write_text("service:\n  item:\n    - '.one'\n    - '.two'\n", encoding="utf-8")
    resolver = SelectorResolver(config)

    try:
        asyncio.run(resolver.find_first(_ClosedPage(), "service.item"))
    except BrowserTargetClosedError as error:
        assert error.retryable is True
    else:
        raise AssertionError("closed target was converted to selector-not-found")


def test_structured_error_event_is_redacted_and_sqlite_compatible(tmp_path: Path) -> None:
    error = PipelineError(
        "patient test@example.com phone 0901 234 567 token=secret",
        phase="CDHA_UPLOADING",
        operation="resolve_upload_frame",
    )
    payload = build_error_event_details(
        error,
        attempt=2,
        browser_url="https://cdha.ai/result?token=secret#private",
        selector_key="cdha.upload_frame",
    )
    rendered = str(payload)
    assert "test@example.com" not in rendered
    assert "0901 234 567" not in rendered
    assert "secret" not in rendered
    assert payload["browser_url"] == "https://cdha.ai/result"
    assert payload["attempt"] == 2

    repository = JobRepository(tmp_path / "jobs.sqlite3")
    repository.initialize()
    job = repository.create_job("https://facebook.com/reel/1")
    repository.record_error(job.job_id, error, attempt=2)
    stored = repository.list_events(job.job_id)[-1]
    assert stored.details["error_code"] == "PIPELINE_ERROR"
    assert stored.details["attempt"] == 2
