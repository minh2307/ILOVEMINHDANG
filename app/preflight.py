from __future__ import annotations

import importlib.util
import sqlite3
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.config.facebook_browser import FacebookBrowserConfig
from app.config.settings import CookieConfigurationError, Settings


class PreflightError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PreflightReport:
    python_version: str
    browser_executable: str
    database_path: str
    queue_database_path: str
    ollama_checked: bool
    profile_path: str
    browser_lock_path: str
    facebook_cookie_path: str
    facebook_cookie_status: str
    configuration_fingerprint: str


def _assert_writable_directory(path: Path, label: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise PreflightError(f"{label} is not a directory: {path}")


def _check_sqlite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(path, timeout=5) as connection:
            connection.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error as exc:
        raise PreflightError(f"SQLite database is unavailable: {path}: {exc}") from exc


def _check_ollama(settings: Settings) -> None:
    endpoint = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            if response.status >= 400:
                raise PreflightError(f"Ollama returned HTTP {response.status}: {endpoint}")
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError(f"Ollama is required but unavailable at {endpoint}: {exc}") from exc


def run_preflight(
    settings: Settings,
    browser_config: FacebookBrowserConfig | None = None,
    *,
    require_ollama: bool = False,
) -> PreflightReport:
    """Validate all prerequisites before a worker dequeues jobs or creates a lock."""
    if sys.version_info < (3, 10):
        raise PreflightError(f"Python 3.10+ is required; found {sys.version.split()[0]}")
    if importlib.util.find_spec("playwright") is None:
        raise PreflightError(
            "Playwright is not installed in this Python environment. "
            "Run .venv/bin/python -m pip install -r requirements.txt"
        )

    try:
        settings.validate()
    except (TypeError, ValueError) as exc:
        raise PreflightError(str(exc)) from exc

    config = browser_config or FacebookBrowserConfig.from_settings(settings)
    try:
        settings.assert_browser_config_matches(config, "preflight")
        cookie = settings.inspect_facebook_cookie()
    except (ValueError, CookieConfigurationError) as exc:
        raise PreflightError(str(exc)) from exc
    executable = config.executable_path
    if not executable.is_file():
        fallback = settings.chrome_executable_fallback
        if fallback.is_file():
            executable = fallback
        else:
            raise PreflightError(
                f"Chrome browser binary not found: {config.executable_path} "
                f"(fallback: {fallback})"
            )

    settings.ensure_runtime_directories()
    config.ensure_directories()
    for path, label in (
        (config.lock_path.parent, "Browser lock directory"),
        (config.profile_path, "Facebook profile directory"),
        (settings.log_dir, "Log directory"),
        (settings.job_data_dir, "Job data directory"),
    ):
        _assert_writable_directory(path, label)
    _check_sqlite(settings.database_path)
    _check_sqlite(config.queue_database_path)
    if require_ollama:
        if not settings.ollama_model:
            raise PreflightError("OLLAMA_MODEL is required for the requested Ollama step")
        _check_ollama(settings)

    return PreflightReport(
        python_version=sys.version.split()[0],
        browser_executable=str(executable),
        database_path=str(settings.database_path),
        queue_database_path=str(config.queue_database_path),
        ollama_checked=require_ollama,
        profile_path=str(config.profile_path),
        browser_lock_path=str(config.lock_path),
        facebook_cookie_path=str(cookie.path),
        facebook_cookie_status=cookie.status,
        configuration_fingerprint=settings.configuration_fingerprint(),
    )
