from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config.settings import Settings


@dataclass(frozen=True, slots=True)
class FacebookBrowserConfig:
    """Derived browser view over the authoritative :class:`Settings` object."""

    browser: str
    framework: str
    cdp_host: str
    cdp_port: int
    profile_path: Path
    executable_path: Path
    headless: bool
    startup_timeout_seconds: float
    lock_timeout_seconds: float
    lock_heartbeat_seconds: float
    lock_wait_timeout_seconds: float
    lock_retry_interval_seconds: float
    retry_delay_seconds: float
    max_start_attempts: int
    max_job_retries: int
    save_diagnostic_html: bool
    lock_path: Path
    pid_path: Path
    diagnostics_path: Path
    downloads_path: Path
    queue_database_path: Path

    @property
    def cdp_url(self) -> str:
        return f"http://{self.cdp_host}:{self.cdp_port}"

    @classmethod
    def from_settings(cls, settings: Settings) -> "FacebookBrowserConfig":
        config = cls(
            browser="chrome",
            framework="playwright",
            cdp_host=settings.browser_cdp_host,
            cdp_port=settings.browser_cdp_port,
            profile_path=settings.chrome_profile_dir.resolve(),
            executable_path=settings.chrome_executable_fallback.resolve(),
            headless=settings.headless,
            startup_timeout_seconds=settings.browser_startup_timeout_seconds,
            lock_timeout_seconds=settings.browser_lock_timeout_seconds,
            lock_heartbeat_seconds=settings.browser_lock_heartbeat_seconds,
            lock_wait_timeout_seconds=settings.browser_lock_wait_timeout_seconds,
            lock_retry_interval_seconds=settings.browser_lock_retry_interval_seconds,
            retry_delay_seconds=settings.retry_initial_delay_seconds,
            max_start_attempts=settings.browser_max_start_attempts,
            max_job_retries=max(
                settings.max_download_retries,
                settings.max_cdha_retries,
                settings.max_facebook_prepare_retries,
            ),
            save_diagnostic_html=settings.save_diagnostic_html,
            lock_path=settings.browser_lock_path.resolve(),
            pid_path=settings.browser_pid_path.resolve(),
            diagnostics_path=settings.diagnostic_directory.resolve(),
            downloads_path=settings.browser_download_dir.resolve(),
            queue_database_path=settings.database_path.resolve(),
        )
        settings.assert_browser_config_matches(config, "FacebookBrowserConfig")
        return config

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        *,
        env_file: Path | None = None,
    ) -> "FacebookBrowserConfig":
        """Compatibility factory; all values come from ``Settings.from_env``.

        ``browser.yaml`` is retained only as migration evidence. Passing an
        alternate YAML path is rejected so a caller cannot reactivate a second
        set of defaults silently.
        """
        if path is not None:
            raise ValueError(
                "Standalone browser YAML is inactive; configure canonical settings in .env"
            )
        return cls.from_settings(Settings.from_env(env_file=env_file))

    def ensure_directories(self) -> None:
        for directory in (
            self.profile_path,
            self.lock_path.parent,
            self.pid_path.parent,
            self.diagnostics_path,
            self.downloads_path,
            self.queue_database_path.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)
