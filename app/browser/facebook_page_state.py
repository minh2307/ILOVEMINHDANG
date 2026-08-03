from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.error_events import safe_browser_url
from app.logging_setup import mask_sensitive
from app.services.privacy_service import PrivacyService


class FacebookPageState(StrEnum):
    LOGGED_IN = "logged_in"
    LOGIN_REQUIRED = "login_required"
    CHECKPOINT = "checkpoint"
    TWO_FACTOR = "two_factor"
    CONSENT_DIALOG = "consent_dialog"
    ACCOUNT_DISABLED = "account_disabled"
    SESSION_EXPIRED = "session_expired"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


LOGIN_TEXTS = ("log in", "đăng nhập")
CHECKPOINT_TEXTS = (
    "security check", "kiểm tra bảo mật", "confirm your identity", "xác nhận danh tính",
)
TWO_FACTOR_TEXTS = (
    "two-factor authentication", "xác thực hai yếu tố", "enter login code", "nhập mã đăng nhập",
)
ACCOUNT_DISABLED_TEXTS = (
    "account has been disabled", "account is disabled", "tài khoản của bạn đã bị vô hiệu hóa",
    "tài khoản đã bị vô hiệu hóa",
)
CONSENT_TEXTS = (
    "allow essential and optional cookies", "chỉ cho phép cookie thiết yếu",
    "cookie thiết yếu và cookie không bắt buộc",
)
SESSION_EXPIRED_TEXTS = ("session expired", "phiên đã hết hạn", "please log in again", "vui lòng đăng nhập lại")
NETWORK_ERROR_TEXTS = (
    "this page isn't available right now", "site can't be reached", "network error",
    "trang này hiện không hiển thị", "try again", "thử lại",
)
RATE_LIMIT_TEXTS = (
    "temporarily blocked", "too many requests", "try again later", "tạm thời bị chặn",
    "hãy thử lại sau",
)


@dataclass(frozen=True, slots=True)
class SelectorProbeResult:
    matched: bool
    selector: str | None
    category: str
    elapsed_ms: int
    visible: bool
    error: str | None = None
    frame_url: str = ""


@dataclass(frozen=True, slots=True)
class FacebookDetectionResult:
    state: FacebookPageState
    probes: tuple[SelectorProbeResult, ...]
    url: str
    title: str
    frames: tuple[str, ...]
    has_dialog: bool
    elapsed_ms: int


@dataclass(frozen=True, slots=True)
class _Indicator:
    category: str
    selector: str


_INDICATORS = (
    _Indicator("logged_in", '[role="navigation"]'),
    _Indicator("logged_in", '[data-testid="account-menu"]'),
    _Indicator("logged_in", '[data-pagelet^="FeedUnit"]'),
    _Indicator("logged_in", '[role="button"][aria-label*="create post" i]'),
    _Indicator("logged_in", '[role="button"][aria-label*="tạo bài viết" i]'),
    _Indicator("logged_in", 'div[role="navigation"] a[aria-label="Trang chủ" i]'),
    _Indicator("logged_in", 'div[role="navigation"] a[aria-label="Home" i]'),
    _Indicator("logged_in", 'svg[aria-label="Tài khoản" i]'),
    _Indicator("logged_in", 'svg[aria-label="Account" i]'),
    _Indicator("login", 'input[name="email"]'),
    _Indicator("login", 'input[name="pass"]'),
    _Indicator("checkpoint", 'form[action*="checkpoint"]'),
    _Indicator("two_factor", 'input[name="approvals_code"]'),
    _Indicator("dialog", '[role="dialog"]'),
)


class FacebookStateDetector:
    def __init__(
        self,
        *,
        timeout_seconds: float = 15,
        probe_timeout_ms: int = 1000,
        logger: logging.Logger | None = None,
    ) -> None:
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.probe_timeout_ms = max(10, int(probe_timeout_ms))
        self.logger = logger or logging.getLogger("cdha_pipeline.facebook_state")
        self._privacy = PrivacyService()

    async def _probe(self, frame: Any, indicator: _Indicator) -> SelectorProbeResult:
        started = time.monotonic()
        try:
            locator = frame.locator(indicator.selector).first
            count = await asyncio.wait_for(
                locator.count(), timeout=self.probe_timeout_ms / 1000
            )
            visible = False
            if count:
                visible = bool(await asyncio.wait_for(
                    locator.is_visible(), timeout=self.probe_timeout_ms / 1000
                ))
            return SelectorProbeResult(
                matched=bool(count), selector=indicator.selector,
                category=indicator.category,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                visible=visible, frame_url=safe_browser_url(str(getattr(frame, "url", ""))),
            )
        except Exception as exc:
            return SelectorProbeResult(
                matched=False, selector=indicator.selector,
                category=indicator.category,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                visible=False, error=f"{type(exc).__name__}: {exc}",
                frame_url=safe_browser_url(str(getattr(frame, "url", ""))),
            )

    async def _detect(self, page: Any) -> FacebookDetectionResult:
        started = time.monotonic()
        url = str(getattr(page, "url", ""))
        title = self._privacy.mask(await page.title())
        frames = tuple(getattr(page, "frames", None) or (page,))
        tasks = [self._probe(frame, indicator) for frame in frames for indicator in _INDICATORS]
        probes = list(await asyncio.gather(*tasks))
        body_text = ""
        try:
            body_text = await asyncio.wait_for(
                page.locator("body").inner_text(), timeout=self.probe_timeout_ms / 1000
            )
            probes.append(SelectorProbeResult(
                matched=bool(body_text.strip()), selector="body", category="page_text",
                elapsed_ms=0, visible=True,
            ))
        except Exception as exc:
            probes.append(SelectorProbeResult(
                matched=False, selector="body", category="page_text", elapsed_ms=0,
                visible=False, error=f"{type(exc).__name__}: {exc}",
            ))
        state = self._classify(url, body_text, probes)
        result = FacebookDetectionResult(
            state=state,
            probes=tuple(probes),
            url=safe_browser_url(url),
            title=title,
            frames=tuple(safe_browser_url(str(getattr(frame, "url", ""))) for frame in frames),
            has_dialog=any(p.category == "dialog" and p.visible for p in probes),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        self.logger.info(
            "Facebook page state detected",
            extra={
                "component": "facebook_state", "event": "FACEBOOK_STATE_DETECTED",
                "details": {
                    "state": state.value, "url": result.url, "title": title,
                    "frame_count": len(frames), "has_dialog": result.has_dialog,
                    "elapsed_ms": result.elapsed_ms,
                },
            },
        )
        return result

    async def detect(self, page: Any) -> FacebookDetectionResult:
        started = time.monotonic()
        try:
            return await asyncio.wait_for(self._detect(page), timeout=self.timeout_seconds)
        except TimeoutError:
            return FacebookDetectionResult(
                state=FacebookPageState.UNKNOWN,
                probes=(SelectorProbeResult(False, None, "detection", int((time.monotonic() - started) * 1000), False, "detection timeout"),),
                url=safe_browser_url(str(getattr(page, "url", ""))), title="",
                frames=(), has_dialog=False,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )

    @staticmethod
    def _contains(text: str, candidates: tuple[str, ...]) -> bool:
        folded = text.casefold()
        return any(candidate in folded for candidate in candidates)

    def _classify(
        self, url: str, text: str, probes: list[SelectorProbeResult]
    ) -> FacebookPageState:
        folded_url = url.casefold()
        visible = {probe.category for probe in probes if probe.visible}
        if self._contains(text, ACCOUNT_DISABLED_TEXTS):
            return FacebookPageState.ACCOUNT_DISABLED
        if "two_factor" in folded_url or "two_factor" in visible or self._contains(text, TWO_FACTOR_TEXTS):
            return FacebookPageState.TWO_FACTOR
        if "/checkpoint" in folded_url or "checkpoint" in visible or self._contains(text, CHECKPOINT_TEXTS):
            return FacebookPageState.CHECKPOINT
        if self._contains(text, RATE_LIMIT_TEXTS):
            return FacebookPageState.RATE_LIMITED
        if "logged_in" in visible:
            return FacebookPageState.LOGGED_IN
        if self._contains(text, ("bạn đang nghĩ gì", "what's on your mind")):
            return FacebookPageState.LOGGED_IN
        if self._contains(text, NETWORK_ERROR_TEXTS) or not text.strip():
            return FacebookPageState.NETWORK_ERROR
        if self._contains(text, SESSION_EXPIRED_TEXTS):
            return FacebookPageState.SESSION_EXPIRED
        if "/login" in folded_url or "login" in visible or self._contains(text, LOGIN_TEXTS):
            return FacebookPageState.LOGIN_REQUIRED
        if self._contains(text, CONSENT_TEXTS):
            return FacebookPageState.CONSENT_DIALOG
        return FacebookPageState.UNKNOWN

    def _sanitize_html(self, html: str) -> str:
        value = re.sub(
            r'(?is)(<input\b[^>]*type=["\']password["\'][^>]*value=["\'])[^"\']*',
            r'\1[REDACTED]', html,
        )
        return mask_sensitive(self._privacy.mask(value))

    async def save_unknown_artifacts(
        self,
        page: Any,
        result: FacebookDetectionResult,
        *,
        root: Path,
        job_id: str,
        browser_profile: str,
        browser_port: int,
    ) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        folder = Path(root) / job_id / timestamp
        folder.mkdir(parents=True, exist_ok=True)
        screenshot = folder / "screenshot.png"
        html_path = folder / "page.html"
        metadata_path = folder / "metadata.json"
        masks = []
        for selector in ("input", "textarea", '[contenteditable="true"]', '[data-testid*="profile"]'):
            try:
                masks.append(page.locator(selector))
            except Exception:
                continue
        await page.screenshot(path=str(screenshot), full_page=True, mask=masks, mask_color="#000000")
        html_path.write_text(self._sanitize_html(await page.content()), encoding="utf-8")
        metadata_path.write_text(
            json.dumps({
                "job_id": job_id,
                "url": result.url,
                "title": result.title,
                "detected_state": result.state.value,
                "timestamp": datetime.now(UTC).isoformat(),
                "selectors_tested": [asdict(probe) for probe in result.probes],
                "frames": list(result.frames),
                "has_dialog": result.has_dialog,
                "browser_profile": browser_profile,
                "browser_port": int(browser_port),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        for path in (screenshot, html_path, metadata_path):
            path.chmod(0o600)
        return folder


async def detect_facebook_page_state(page: Any) -> FacebookPageState:
    return (await FacebookStateDetector().detect(page)).state
