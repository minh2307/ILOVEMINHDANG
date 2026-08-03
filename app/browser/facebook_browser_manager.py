from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from app.config.facebook_browser import FacebookBrowserConfig
from app.config.settings import Settings
from app.infrastructure.browser.file_browser_lock import BrowserLockUnavailable, FileBrowserLock
from app.error_events import safe_browser_url
from app.services.privacy_service import PrivacyService


class FacebookBrowserError(RuntimeError):
    pass


class ProfileInUseError(FacebookBrowserError):
    pass


class FacebookBrowserManager:
    """The only component allowed to start Chrome or connect Playwright over CDP."""

    def __init__(
        self,
        settings: Settings | None = None,
        logger: logging.Logger | None = None,
        *,
        config: FacebookBrowserConfig | None = None,
        browser_lock: FileBrowserLock | None = None,
    ) -> None:
        self.settings = settings
        if config is None:
            canonical_settings = settings or Settings.from_env()
            config = FacebookBrowserConfig.from_settings(canonical_settings)
            self.settings = canonical_settings
        elif settings is not None:
            settings.assert_browser_config_matches(config, "browser manager")
        self.config = config
        self.logger = logger or logging.getLogger("cdha_pipeline.facebook_browser")
        self.browser_lock = browser_lock or FileBrowserLock(
            str(config.lock_path),
            process_name="cdha-browser-manager",
            browser_profile=str(config.profile_path),
            browser_port=config.cdp_port,
            timeout_seconds=config.lock_timeout_seconds,
            heartbeat_seconds=config.lock_heartbeat_seconds,
        )
        self.playwright: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.tabs: Any = None
        self.browser_process_id: int | None = None
        self._owns_browser_lock = False
        self._privacy = PrivacyService()
        self._started_process: subprocess.Popen[bytes] | None = None

    async def __aenter__(self) -> "FacebookBrowserManager":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def is_cdp_ready(self) -> bool:
        def probe() -> bool:
            try:
                with urlopen(f"{self.config.cdp_url}/json/version", timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return bool(payload.get("Browser") and payload.get("webSocketDebuggerUrl"))
            except (OSError, URLError, ValueError, json.JSONDecodeError):
                return False
        return await asyncio.to_thread(probe)

    def _profile_looks_locked(self) -> bool:
        return any((self.config.profile_path / name).exists() for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"))

    @staticmethod
    def _listening_pids(port: int) -> list[int]:
        """Resolve a local TCP listener through /proc without killing or mutating it."""
        inodes: set[str] = set()
        target = f"{int(port):04X}"
        for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
            try:
                for line in table.read_text(encoding="ascii").splitlines()[1:]:
                    fields = line.split()
                    if len(fields) > 9 and fields[1].rsplit(":", 1)[-1].upper() == target and fields[3] == "0A":
                        inodes.add(fields[9])
            except (FileNotFoundError, PermissionError, OSError):
                continue
        owners: list[int] = []
        if not inodes:
            return owners
        for process_dir in Path("/proc").iterdir():
            if not process_dir.name.isdigit():
                continue
            try:
                for descriptor in (process_dir / "fd").iterdir():
                    if descriptor.readlink().as_posix() in {f"socket:[{inode}]" for inode in inodes}:
                        owners.append(int(process_dir.name))
                        break
            except (FileNotFoundError, PermissionError, OSError):
                continue
        return owners

    @staticmethod
    def _process_command(pid: int) -> str:
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(
                b"\x00", b" "
            ).decode("utf-8", "replace")
        except (FileNotFoundError, PermissionError, OSError):
            return ""

    def _matches_managed_chrome(self, pid: int) -> bool:
        command = self._process_command(pid)
        return (
            f"--user-data-dir={self.config.profile_path}" in command
            and f"--remote-debugging-port={self.config.cdp_port}" in command
        )

    def _validate_cdp_owner(self) -> int | None:
        recorded = self._read_managed_pid()
        if recorded is not None:
            return recorded
        owners = self._listening_pids(self.config.cdp_port)
        matching = [pid for pid in owners if self._matches_managed_chrome(pid)]
        if matching:
            self._write_pid_atomic(matching[0])
            return matching[0]
        if owners:
            details = [
                {"pid": pid, "command": self._privacy.mask(self._process_command(pid))}
                for pid in owners
            ]
            self.logger.error("CDP port is owned by an unmanaged process", extra={
                "component": "browser_manager", "event": "UNMANAGED_CDP_PORT",
                "cdp_port": self.config.cdp_port, "details": details,
            })
            raise FacebookBrowserError(
                f"Refusing unmanaged process on CDP port {self.config.cdp_port}"
            )
        self.logger.warning("CDP responded but listener PID could not be resolved", extra={
            "component": "browser_manager", "event": "CDP_OWNER_UNRESOLVED",
            "cdp_port": self.config.cdp_port,
        })
        return None

    def _write_pid_atomic(self, pid: int) -> None:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{self.config.pid_path.name}.", dir=self.config.pid_path.parent
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(str(pid))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.config.pid_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _reject_unverified_profile_markers(self) -> None:
        markers = [
            self.config.profile_path / name
            for name in ("SingletonLock", "SingletonSocket", "SingletonCookie")
            if (self.config.profile_path / name).exists()
        ]
        if not markers:
            return
        pids = [
            pid for pid in self._listening_pids(self.config.cdp_port)
            if self._matches_managed_chrome(pid)
        ]
        if pids:
            raise ProfileInUseError(
                f"Canonical Chrome profile is active in PID {pids[0]}: {self.config.profile_path}"
            )
        raise ProfileInUseError(
            "Chrome profile markers exist but no verified managed PID was found; "
            f"no profile data was modified: {self.config.profile_path}"
        )

    def _chrome_command(self) -> list[str]:
        command = [
            str(self.config.executable_path),
            f"--remote-debugging-port={self.config.cdp_port}",
            f"--remote-debugging-address={self.config.cdp_host}",
            f"--user-data-dir={self.config.profile_path}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-notifications",
        ]
        if self.config.headless:
            command.append("--headless=new")
        return command

    async def ensure_chrome(self) -> None:
        """Start or verify Chrome only while the canonical browser lock is held."""
        if getattr(self.browser_lock, "owner_token", None):
            await self._ensure_chrome_locked()
            return
        try:
            async with self.browser_lock.hold("browser-manager:start"):
                await self._ensure_chrome_locked()
        except BrowserLockUnavailable as exc:
            raise ProfileInUseError(
                f"Canonical browser lock is held for profile {self.config.profile_path}"
            ) from exc

    async def _ensure_chrome_locked(self) -> None:
        self.config.ensure_directories()
        if await self.is_cdp_ready():
            self.browser_process_id = self._validate_cdp_owner()
            return
        if self._profile_looks_locked():
            self._reject_unverified_profile_markers()
        if not self.config.executable_path.is_file():
            raise FacebookBrowserError(
                f"Chrome executable not found: {self.config.executable_path}"
            )
        for attempt in range(1, self.config.max_start_attempts + 1):
            if await self.is_cdp_ready():
                self.browser_process_id = self._validate_cdp_owner()
                return
            self._started_process = subprocess.Popen(
                self._chrome_command(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.browser_process_id = self._started_process.pid
            self._write_pid_atomic(self.browser_process_id)
            deadline = time.monotonic() + self.config.startup_timeout_seconds
            while time.monotonic() < deadline:
                if await self.is_cdp_ready():
                    self.logger.info(
                        "Facebook Chrome ready",
                        extra={
                            "browser_process_id": self.browser_process_id,
                            "cdp_port": self.config.cdp_port,
                            "profile_path": str(self.config.profile_path),
                        },
                    )
                    return
                if self._started_process.poll() is not None:
                    break
                await asyncio.sleep(self.config.retry_delay_seconds)
            if attempt < self.config.max_start_attempts:
                await asyncio.sleep(self.config.retry_delay_seconds)
        raise FacebookBrowserError(
            f"Chrome did not expose CDP after {self.config.max_start_attempts} bounded attempts"
        )

    async def start(self) -> Any:
        if self.context is not None:
            return self.context
        if not getattr(self.browser_lock, "owner_token", None):
            if not await self.browser_lock.acquire("browser-manager:session"):
                raise ProfileInUseError(
                    f"Canonical browser lock is held for profile {self.config.profile_path}"
                )
            self.browser_lock.start_heartbeat()
            self._owns_browser_lock = True
        try:
            await self.ensure_chrome()
            from playwright.async_api import async_playwright
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.connect_over_cdp(self.config.cdp_url)
            if not self.browser.contexts:
                raise FacebookBrowserError("Shared Chrome has no default browser context")
            self.context = self.browser.contexts[0]
            timeout = int(getattr(self.settings, "page_timeout_seconds", 60) * 1000)
            self.context.set_default_timeout(timeout)
            self.context.set_default_navigation_timeout(timeout)
            from app.browser.facebook_tab_manager import FacebookTabManager
            self.tabs = FacebookTabManager(self.context)
            return self.context
        except Exception:
            await self.close()
            raise

    async def new_page(self) -> Any:
        context = await self.start()
        return await context.new_page()

    async def wait_for_manual_action(self, prompt: str, completion_check: Callable[[], Awaitable[bool]]) -> None:
        print(prompt)
        await asyncio.to_thread(input, "Press ENTER after completing the action in Chrome: ")
        if not await completion_check():
            raise FacebookBrowserError("Manual action was not completed or could not be verified")

    async def save_diagnostics(self, page: Any, output_dir: Path, name: str) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        screenshot = (output_dir / f"{name}.png").resolve()
        metadata = (output_dir / f"{name}.json").resolve()
        await page.screenshot(path=str(screenshot), full_page=True)
        metadata.write_text(json.dumps({"url": safe_browser_url(str(page.url)), "title": self._privacy.mask(await page.title())}, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact = metadata
        if getattr(self.settings, "save_diagnostic_html", self.config.save_diagnostic_html):
            artifact = (output_dir / f"{name}.html").resolve()
            html = await page.content()
            html = re.sub(
                r'(?i)(\b(?:value|data-token|data-access-token)\s*=\s*)(["\']).*?\2',
                r'\1"[REDACTED]"',
                html,
            )
            artifact.write_text(self._privacy.mask(html), encoding="utf-8")
        for path in {screenshot, metadata, artifact}:
            path.chmod(0o600)
        return screenshot, artifact

    async def close(self) -> None:
        """Disconnect automation only. Never closes shared browser or default context."""
        self.tabs = None
        self.context = None
        self.browser = None
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None
        if self._owns_browser_lock:
            await self.browser_lock.stop_heartbeat()
            await self.browser_lock.release()
            self._owns_browser_lock = False

    async def shutdown_browser(self, force: bool = False) -> bool:
        """Explicitly stop only the Chrome PID recorded by this system."""
        pid = self._read_managed_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
        self.config.pid_path.unlink(missing_ok=True)
        return True

    def _read_managed_pid(self) -> int | None:
        try:
            value = int(self.config.pid_path.read_text(encoding="ascii").strip())
            os.kill(value, 0)
            command = Path(f"/proc/{value}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            expected_profile = f"--user-data-dir={self.config.profile_path}"
            expected_port = f"--remote-debugging-port={self.config.cdp_port}"
            if expected_profile not in command or expected_port not in command:
                self.logger.error("Refusing unmanaged PID from Facebook Chrome pidfile", extra={"browser_process_id": value})
                return None
            return value
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError, OSError):
            return None

    @staticmethod
    def _looks_like_chrome_profile_conflict(exc: BaseException) -> bool:
        message = str(exc).lower()
        return any(marker in message for marker in ("processsingleton", "singletonlock", "profile appears to be in use", "user data directory is already in use"))

    @staticmethod
    def _looks_like_missing_chrome_channel(exc: BaseException) -> bool:
        message = str(exc).lower()
        return "distribution 'chrome' is not found" in message or "executable doesn't exist" in message or "executable does not exist" in message
