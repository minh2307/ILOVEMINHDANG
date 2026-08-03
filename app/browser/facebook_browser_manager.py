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
from app.domain.models.browser_health import BrowserHealth, BrowserHealthState
from app.errors import (
    BrowserContextClosedError,
    BrowserDisconnectedError,
    BrowserPageClosedError,
    BrowserPageOwnershipError,
)
from app.infrastructure.browser.file_browser_lock import BrowserLockUnavailable, FileBrowserLock
from app.error_events import safe_browser_url, safe_error_message
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
        self._context_closed = False
        self._browser_disconnected = False
        self._owned_pages: dict[int, tuple[Any, str]] = {}

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
            self._context_closed = False
            self._browser_disconnected = False
            if hasattr(self.browser, "on"):
                self.browser.on(
                    "disconnected",
                    lambda: setattr(self, "_browser_disconnected", True),
                )
            if hasattr(self.context, "on"):
                self.context.on(
                    "close", lambda: setattr(self, "_context_closed", True)
                )
            action_timeout = int(
                getattr(self.settings, "browser_action_timeout_seconds", 60) * 1000
            )
            navigation_timeout = int(
                getattr(self.settings, "browser_navigation_timeout_seconds", 60)
                * 1000
            )
            self.context.set_default_timeout(action_timeout)
            self.context.set_default_navigation_timeout(navigation_timeout)
            from app.browser.facebook_tab_manager import FacebookTabManager
            self.tabs = FacebookTabManager(self.context)
            return self.context
        except Exception:
            await self.close()
            raise

    async def new_page(self) -> Any:
        return await self.acquire_page("compatibility:new_page")

    async def acquire_page(self, purpose: str) -> Any:
        context = await self.start()
        await self.ensure_connected()
        try:
            page = await context.new_page()
        except Exception as exc:
            raise BrowserContextClosedError(
                str(exc),
                phase="BROWSER_SESSION",
                operation="acquire_page",
                details={"purpose": purpose},
            ) from exc
        self._owned_pages[id(page)] = (page, str(purpose))
        return page

    async def release_page(self, page: Any) -> bool:
        owned = self._owned_pages.pop(id(page), None)
        if owned is None or owned[0] is not page:
            raise BrowserPageOwnershipError(
                "Refusing to close a page not owned by the browser manager",
                phase="BROWSER_SESSION",
                operation="release_page",
            )
        try:
            if bool(page.is_closed()):
                return False
            await page.close()
            return True
        except Exception as exc:
            raise BrowserPageClosedError(
                str(exc),
                phase="BROWSER_SESSION",
                operation="release_page",
                details={"purpose": owned[1]},
            ) from exc

    async def get_health(self, page: Any | None = None) -> BrowserHealth:
        browser_connected = False
        if self.browser is not None and not self._browser_disconnected:
            try:
                connected = getattr(self.browser, "is_connected", None)
                browser_connected = bool(
                    connected() if callable(connected) else connected
                )
            except Exception:
                browser_connected = False
        if not browser_connected:
            return BrowserHealth(
                BrowserHealthState.DISCONNECTED,
                browser_connected=False,
                context_available=self.context is not None,
                page_available=None if page is None else False,
            )
        if self.context is None or self._context_closed:
            return BrowserHealth(
                BrowserHealthState.CONTEXT_CLOSED,
                browser_connected=True,
                context_available=False,
                page_available=None if page is None else False,
            )
        if page is not None:
            try:
                if bool(page.is_closed()):
                    return BrowserHealth(
                        BrowserHealthState.PAGE_CLOSED,
                        browser_connected=True,
                        context_available=True,
                        page_available=False,
                    )
            except Exception:
                return BrowserHealth(
                    BrowserHealthState.UNKNOWN,
                    browser_connected=True,
                    context_available=True,
                    page_available=None,
                )
        current_url = None
        if page is not None:
            try:
                current_url = safe_browser_url(str(page.url))
            except Exception:
                current_url = None
        return BrowserHealth(
            BrowserHealthState.CONNECTED,
            browser_connected=True,
            context_available=True,
            page_available=None if page is None else True,
            current_url=current_url,
        )

    async def ensure_connected(self, page: Any | None = None) -> None:
        health = await self.get_health(page)
        if health.state is BrowserHealthState.CONNECTED:
            return
        metadata = {
            "browser_health_state": health.state.value,
            "current_url": health.current_url,
        }
        if health.state is BrowserHealthState.PAGE_CLOSED:
            raise BrowserPageClosedError(
                "Browser page is closed",
                phase="BROWSER_SESSION",
                operation="ensure_connected",
                details=metadata,
            )
        if health.state is BrowserHealthState.CONTEXT_CLOSED:
            raise BrowserContextClosedError(
                "Shared browser context is closed",
                phase="BROWSER_SESSION",
                operation="ensure_connected",
                details=metadata,
            )
        raise BrowserDisconnectedError(
            "Shared browser connection is disconnected",
            phase="BROWSER_SESSION",
            operation="ensure_connected",
            details=metadata,
        )

    async def wait_for_manual_action(self, prompt: str, completion_check: Callable[[], Awaitable[bool]]) -> None:
        print(prompt)
        await asyncio.to_thread(input, "Press ENTER after completing the action in Chrome: ")
        if not await completion_check():
            raise FacebookBrowserError("Manual action was not completed or could not be verified")

    @classmethod
    def _safe_diagnostic_details(cls, value: Any, *, key: str = "") -> Any:
        blocked = {
            "authorization", "access_token", "token", "password",
            "cookie", "cookies", "storage_state",
        }
        if key.casefold() in blocked:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                str(item_key): cls._safe_diagnostic_details(
                    item_value, key=str(item_key)
                )
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._safe_diagnostic_details(item) for item in value]
        if isinstance(value, str):
            return safe_error_message(value)
        return value

    async def save_diagnostics(
        self,
        page: Any,
        output_dir: Path,
        name: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> tuple[Path, ...]:
        output_dir.mkdir(parents=True, exist_ok=True)
        screenshot = (output_dir / f"{name}.png").resolve()
        metadata = (output_dir / f"{name}.json").resolve()
        health = await self.get_health(page)
        page_url = health.current_url
        if page_url is None:
            try:
                page_url = safe_browser_url(str(page.url))
            except Exception:
                page_url = None
        payload: dict[str, Any] = {
            "browser_health_state": health.state.value,
            "url": page_url,
            **self._safe_diagnostic_details(details or {}),
        }
        artifacts: list[Path] = []
        page_closed = health.state is BrowserHealthState.PAGE_CLOSED
        if not page_closed and hasattr(page, "is_closed"):
            try:
                page_closed = bool(page.is_closed())
            except Exception:
                page_closed = True
        html_artifact: Path | None = None
        if not page_closed:
            try:
                payload["title"] = self._privacy.mask(await page.title())
                await page.screenshot(path=str(screenshot), full_page=True)
                artifacts.append(screenshot)
            except Exception as exc:
                payload["capture_error"] = type(exc).__name__
            if getattr(
                self.settings, "save_diagnostic_html", self.config.save_diagnostic_html
            ):
                try:
                    html_artifact = (output_dir / f"{name}.html").resolve()
                    html = await page.content()
                    html = re.sub(
                        r'(?i)(\b(?:value|data-token|data-access-token)\s*=\s*)(["\']).*?\2',
                        r'\1"[REDACTED]"',
                        html,
                    )
                    html_artifact.write_text(
                        self._privacy.mask(html), encoding="utf-8"
                    )
                    artifacts.append(html_artifact)
                except Exception as exc:
                    payload["html_capture_error"] = type(exc).__name__
        metadata.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        all_artifacts = [*artifacts, metadata]
        for path in set(all_artifacts):
            path.chmod(0o600)
        if screenshot in artifacts:
            return screenshot, html_artifact or metadata
        return (metadata,)

    async def close(self) -> None:
        """Disconnect automation only. Never closes shared browser or default context."""
        for page, _purpose in list(self._owned_pages.values()):
            try:
                await self.release_page(page)
            except (BrowserPageClosedError, BrowserPageOwnershipError):
                self._owned_pages.pop(id(page), None)
        self.tabs = None
        self.context = None
        self.browser = None
        self._context_closed = False
        self._browser_disconnected = False
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

    def managed_pid(self) -> int | None:
        """Return only a PID proven to own the canonical profile and CDP port."""
        return self._read_managed_pid()

    @staticmethod
    def _looks_like_chrome_profile_conflict(exc: BaseException) -> bool:
        message = str(exc).lower()
        return any(marker in message for marker in ("processsingleton", "singletonlock", "profile appears to be in use", "user data directory is already in use"))

    @staticmethod
    def _looks_like_missing_chrome_channel(exc: BaseException) -> bool:
        message = str(exc).lower()
        return "distribution 'chrome' is not found" in message or "executable doesn't exist" in message or "executable does not exist" in message
