from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.error_events import safe_browser_url
from app.services.privacy_service import PrivacyService


_PRIVACY = PrivacyService()


class ChromeManagerError(RuntimeError):
    pass


class ProfileInUseError(ChromeManagerError):
    pass


class ChromeManager:
    def __init__(self, settings: Settings, logger: logging.Logger | None = None):
        self.settings = settings
        self.logger = logger or logging.getLogger("cdha_pipeline.chrome")
        self.playwright: Any = None
        self.context: Any = None
        self._profile_lock_fd: int | None = None
        self._lock_path = self.settings.chrome_profile_dir / ".profile.lock"

    async def __aenter__(self) -> "ChromeManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    def _acquire_profile_lock(self) -> None:
        self.settings.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.fsync(descriptor)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise ProfileInUseError(
                "The CDHA automation Chrome profile is already in use. Close the Chrome "
                f"process using: {self.settings.chrome_profile_dir} and retry."
            ) from exc
        self._profile_lock_fd = descriptor

    def _release_profile_lock(self) -> None:
        descriptor = self._profile_lock_fd
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            self._profile_lock_fd = None

    async def start(self) -> Any:
        if self.context is not None:
            return self.context
        self._acquire_profile_lock()
        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import async_playwright

            self.playwright = await async_playwright().start()
            launch_options = self._launch_options()
            try:
                self.context = await self.playwright.chromium.launch_persistent_context(
                    channel=self.settings.chrome_channel,
                    **launch_options,
                )
                self.logger.info("Started installed Chrome with channel=%s", self.settings.chrome_channel)
            except PlaywrightError as channel_error:
                if not self._looks_like_missing_chrome_channel(channel_error):
                    raise
                allowed_fallbacks = (
                    Path("/usr/bin/google-chrome"),
                    Path("/usr/bin/google-chrome-stable"),
                )
                configured = self.settings.chrome_executable_fallback
                if configured not in allowed_fallbacks:
                    raise ChromeManagerError(
                        "Chrome fallback must be one of the approved installed paths: "
                        "/usr/bin/google-chrome or /usr/bin/google-chrome-stable"
                    ) from channel_error
                fallback = next(
                    (
                        candidate
                        for candidate in (configured, *allowed_fallbacks)
                        if candidate.is_file() and os.access(candidate, os.X_OK)
                    ),
                    None,
                )
                if fallback is None:
                    raise ChromeManagerError(
                        f"Chrome channel '{self.settings.chrome_channel}' is unavailable and "
                        "neither approved installed Chrome path is usable"
                    ) from channel_error
                self.logger.warning(
                    "Chrome channel launch failed; using installed executable fallback",
                    extra={"chrome_path": str(fallback)},
                )
                self.context = await self.playwright.chromium.launch_persistent_context(
                    executable_path=str(fallback),
                    **launch_options,
                )
            self.context.set_default_timeout(self.settings.page_timeout_seconds * 1000)
            self.context.set_default_navigation_timeout(self.settings.page_timeout_seconds * 1000)
            return self.context
        except Exception as exc:
            await self._close_playwright_only()
            self._release_profile_lock()
            if self._looks_like_chrome_profile_conflict(exc):
                raise ProfileInUseError(
                    "Google Chrome reports that the CDHA automation profile is already open. "
                    f"Close the Chrome process using: {self.settings.chrome_profile_dir} and retry."
                ) from exc
            raise

    def _launch_options(self) -> dict[str, Any]:
        return {
            "user_data_dir": str(self.settings.chrome_profile_dir),
            "headless": self.settings.headless,
            "viewport": {
                "width": self.settings.viewport_width,
                "height": self.settings.viewport_height,
            },
            "accept_downloads": True,
            "args": ["--start-maximized", "--disable-notifications"],
        }

    async def new_page(self) -> Any:
        context = await self.start()
        return await context.new_page()

    async def wait_for_manual_action(
        self,
        prompt: str,
        completion_check: Callable[[], Awaitable[bool]],
    ) -> None:
        print(prompt)
        await asyncio.to_thread(input, "Press ENTER after completing the action in Chrome: ")
        if not await completion_check():
            raise ChromeManagerError("Manual action was not completed or could not be verified")

    async def save_diagnostics(self, page: Any, output_dir: Path, name: str) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = (output_dir / f"{name}.png").resolve()
        metadata_path = (output_dir / f"{name}.json").resolve()
        await page.screenshot(path=str(screenshot_path), full_page=True)
        metadata_path.write_text(
            json.dumps(
                {"url": safe_browser_url(str(page.url)), "title": _PRIVACY.mask(await page.title())},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        artifact_path = metadata_path
        if self.settings.save_diagnostic_html:
            artifact_path = (output_dir / f"{name}.html").resolve()
            artifact_path.write_text(await page.content(), encoding="utf-8")
        for path in (screenshot_path, metadata_path, artifact_path):
            path.chmod(0o600)
        self.logger.error(
            "Saved browser diagnostics",
            extra={
                "url": safe_browser_url(str(page.url)),
                "screenshot": str(screenshot_path),
                "metadata": str(metadata_path),
                "html_enabled": self.settings.save_diagnostic_html,
            },
        )
        return screenshot_path, artifact_path

    async def close(self) -> None:
        try:
            if self.context is not None:
                await self.context.close()
        finally:
            self.context = None
            await self._close_playwright_only()
            self._release_profile_lock()

    async def _close_playwright_only(self) -> None:
        if self.playwright is not None:
            try:
                await self.playwright.stop()
            finally:
                self.playwright = None

    @staticmethod
    def _looks_like_chrome_profile_conflict(exc: BaseException) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "processsingleton",
                "singletonlock",
                "profile appears to be in use",
                "user data directory is already in use",
            )
        )

    @staticmethod
    def _looks_like_missing_chrome_channel(exc: BaseException) -> bool:
        message = str(exc).lower()
        return (
            "distribution 'chrome' is not found" in message
            or "executable doesn't exist" in message
            or "executable does not exist" in message
        )
