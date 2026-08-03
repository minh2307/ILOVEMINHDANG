from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.browser.facebook_page_state import (
    FacebookPageState,
    FacebookStateDetector,
    detect_facebook_page_state,
)


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str):
        self.page = page
        self.selector = selector
        self.first = self

    async def count(self) -> int:
        return int(self.selector == "body" or self.selector in self.page.visible)

    async def is_visible(self) -> bool:
        return bool(await self.count())

    async def inner_text(self) -> str:
        return self.page.text if self.selector == "body" else ""


class FakeContext:
    async def cookies(self):
        return [{"name": "c_user", "value": "redacted-at-source"}]


class FakePage:
    def __init__(self, *, url="https://www.facebook.com/", text="", visible=()):
        self.url = url
        self.text = text
        self.visible = set(visible)
        self.frames = [self]
        self.context = FakeContext()

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def title(self) -> str:
        return "Facebook"

    async def screenshot(self, *, path: str, **kwargs) -> None:
        Path(path).write_bytes(b"safe-png")

    async def content(self) -> str:
        return '<html><input type="password" value="secret"><p>alice@example.com token=abc123</p></html>'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (FakePage(visible={'[role="navigation"]'}), FacebookPageState.LOGGED_IN),
        (FakePage(url="https://www.facebook.com/login", text="Log in", visible={'input[name="email"]'}), FacebookPageState.LOGIN_REQUIRED),
        (FakePage(url="https://www.facebook.com/login", text="Đăng nhập", visible={'input[name="pass"]'}), FacebookPageState.LOGIN_REQUIRED),
        (FakePage(url="https://www.facebook.com/checkpoint/", text="Security Check - Confirm your identity"), FacebookPageState.CHECKPOINT),
        (FakePage(url="https://www.facebook.com/checkpoint/", text="Kiểm tra bảo mật - Xác nhận danh tính"), FacebookPageState.CHECKPOINT),
        (FakePage(text="Two-factor authentication - Enter login code"), FacebookPageState.TWO_FACTOR),
        (FakePage(text="Tài khoản của bạn đã bị vô hiệu hóa"), FacebookPageState.ACCOUNT_DISABLED),
        (FakePage(text="Allow essential and optional cookies"), FacebookPageState.CONSENT_DIALOG),
        (FakePage(text="This page isn't available right now. Try again."), FacebookPageState.NETWORK_ERROR),
        (FakePage(text="You're temporarily blocked. Try again later."), FacebookPageState.RATE_LIMITED),
    ],
)
async def test_detects_multilingual_facebook_states(page: FakePage, expected: FacebookPageState):
    result = await FacebookStateDetector(timeout_seconds=0.5, probe_timeout_ms=20).detect(page)
    assert result.state is expected
    assert result.url == page.url
    assert result.elapsed_ms < 500
    assert result.probes


@pytest.mark.asyncio
async def test_public_detection_function_returns_enum():
    state = await detect_facebook_page_state(FakePage(visible={'[role="navigation"]'}))
    assert state is FacebookPageState.LOGGED_IN


@pytest.mark.asyncio
async def test_detection_logging_does_not_overwrite_reserved_log_record_fields(caplog):
    caplog.set_level(logging.INFO, logger="cdha_pipeline.facebook_state")
    result = await FacebookStateDetector().detect(
        FakePage(visible={'[role="navigation"]'})
    )
    assert result.state is FacebookPageState.LOGGED_IN
    assert any(record.event == "FACEBOOK_STATE_DETECTED" for record in caplog.records)


@pytest.mark.asyncio
async def test_unknown_state_saves_privacy_safe_debug_artifacts(tmp_path: Path):
    detector = FacebookStateDetector(timeout_seconds=0.5, probe_timeout_ms=20)
    page = FakePage(url="https://www.facebook.com/unrecognized", text="new layout")
    result = await detector.detect(page)
    assert result.state is FacebookPageState.UNKNOWN
    folder = await detector.save_unknown_artifacts(
        page, result, root=tmp_path, job_id="job-unknown",
        browser_profile="facebook", browser_port=9222,
    )
    assert (folder / "screenshot.png").is_file()
    html = (folder / "page.html").read_text(encoding="utf-8")
    assert "secret" not in html
    assert "alice@example.com" not in html
    assert "abc123" not in html
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["job_id"] == "job-unknown"
    assert metadata["detected_state"] == "unknown"
    assert metadata["browser_port"] == 9222
    assert metadata["selectors_tested"]
