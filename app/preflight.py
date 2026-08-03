from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from app.config.facebook_browser import FacebookBrowserConfig
from app.config.settings import CookieConfigurationError, Settings
from app.error_events import safe_browser_url


class PreflightError(RuntimeError):
    pass


class CheckStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PreflightCheckResult:
    name: str
    category: str
    required: bool
    status: CheckStatus
    message: str
    duration_ms: int
    details: dict[str, Any] = field(default_factory=dict)
    artifact_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "duration_ms", max(0, int(self.duration_ms)))
        object.__setattr__(self, "details", _sanitize(self.details))
        object.__setattr__(
            self, "artifact_paths", tuple(str(path) for path in self.artifact_paths)
        )


@dataclass(frozen=True, slots=True)
class PreflightReport:
    mode: str
    checks: tuple[PreflightCheckResult, ...]
    overall_status: str
    started_at: str
    completed_at: str
    configuration_fingerprint: str
    missing_required_checks: tuple[str, ...] = ()
    report_path: str | None = None
    report_version: str = "1.0"

    @classmethod
    def create(
        cls,
        *,
        mode: str,
        checks: Iterable[PreflightCheckResult],
        started_at: str,
        completed_at: str,
        configuration_fingerprint: str,
        required_check_names: Iterable[str] = (),
        report_path: str | None = None,
    ) -> "PreflightReport":
        materialized = tuple(checks)
        executed = {check.name for check in materialized}
        missing = tuple(sorted(set(required_check_names) - executed))
        required_bad = any(
            check.required and check.status is not CheckStatus.PASSED
            for check in materialized
        )
        optional_bad = any(
            not check.required
            and check.status in {CheckStatus.WARNING, CheckStatus.FAILED}
            for check in materialized
        )
        if missing or required_bad:
            verdict = "FAIL"
        elif optional_bad:
            verdict = "WARN"
        else:
            verdict = "PASS"
        return cls(
            mode=mode,
            checks=materialized,
            overall_status=verdict,
            started_at=started_at,
            completed_at=completed_at,
            configuration_fingerprint=configuration_fingerprint,
            missing_required_checks=missing,
            report_path=report_path,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SENSITIVE_KEYS = (
    "cookie", "token", "password", "secret", "authorization", "storage_state",
    "patient", "html", "prompt", "response",
)

QUICK_REQUIRED_CHECKS = (
    "python_runtime",
    "python_packages",
    "repository_root",
    "typed_settings",
    "runtime_directories",
    "database_schema",
    "chrome_executable",
    "ffmpeg",
    "yt_dlp",
    "ollama_configuration",
    "canonical_paths",
    "adapter_configuration",
    "composition_root",
)

FULL_REQUIRED_CHECKS = QUICK_REQUIRED_CHECKS + (
    "ollama_server",
    "ollama_model",
    "ollama_inference",
    "browser_lock",
    "browser_start",
    "facebook_authentication",
    "facebook_target",
    "cdha_authentication",
    "cdha_selectors",
)


def _sanitize(value: Any, key: str = "") -> Any:
    lowered = key.casefold()
    if any(marker in lowered for marker in _SENSITIVE_KEYS):
        if lowered.endswith(("path", "file", "status", "format", "required")):
            return str(value) if isinstance(value, Path) else value
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    return value


def _result(
    name: str,
    category: str,
    required: bool,
    status: CheckStatus,
    message: str,
    started: float,
    *,
    details: dict[str, Any] | None = None,
    artifact_paths: Iterable[str | Path] = (),
) -> PreflightCheckResult:
    return PreflightCheckResult(
        name=name,
        category=category,
        required=required,
        status=status,
        message=message,
        duration_ms=int((time.monotonic() - started) * 1000),
        details=details or {},
        artifact_paths=tuple(str(path) for path in artifact_paths),
    )


def _run_check(
    name: str,
    category: str,
    required: bool,
    operation: Callable[[], tuple[str, dict[str, Any]]],
) -> PreflightCheckResult:
    started = time.monotonic()
    try:
        message, details = operation()
        return _result(
            name, category, required, CheckStatus.PASSED, message, started,
            details=details,
        )
    except subprocess.TimeoutExpired as exc:
        return _result(
            name, category, required, CheckStatus.TIMEOUT,
            f"{name} timed out", started, details={"error": exc},
        )
    except Exception as exc:
        return _result(
            name, category, required, CheckStatus.FAILED,
            str(exc) or type(exc).__name__, started,
            details={"error_type": type(exc).__name__},
        )


def _python_runtime() -> tuple[str, dict[str, Any]]:
    if sys.version_info < (3, 10):
        raise RuntimeError(f"Python 3.10+ is required; found {sys.version.split()[0]}")
    return f"Python {sys.version.split()[0]} is supported", {
        "version": sys.version.split()[0],
        "interpreter": sys.executable,
        "virtual_environment": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
    }


def _python_packages() -> tuple[str, dict[str, Any]]:
    modules = ("playwright", "yaml", "dotenv", "filelock", "pytest", "yt_dlp", "PIL")
    missing = [name for name in modules if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError("Required Python packages are missing: " + ", ".join(missing))
    return "Required Python packages are importable", {"modules": list(modules)}


def _repository_root(settings: Settings) -> tuple[str, dict[str, Any]]:
    required = ("requirements.txt", "app/main.py", "app/bootstrap.py")
    missing = [name for name in required if not (settings.project_root / name).is_file()]
    if missing:
        raise RuntimeError("Repository root is missing: " + ", ".join(missing))
    if settings.project_root.resolve() != Path(__file__).resolve().parents[1]:
        raise RuntimeError("Settings project root does not match the source repository root")
    return "Repository root resolves independently of the current directory", {
        "path": settings.project_root,
        "required_files": list(required),
    }


def _typed_settings(
    settings: Settings, config: FacebookBrowserConfig
) -> tuple[str, dict[str, Any]]:
    settings.validate()
    settings.assert_browser_config_matches(config, "preflight")
    return "Typed settings and derived browser configuration are valid", {
        "environment": settings.application_environment,
        "fingerprint": settings.configuration_fingerprint(),
    }


def _runtime_directories(
    settings: Settings, config: FacebookBrowserConfig
) -> tuple[str, dict[str, Any]]:
    paths = {
        "runtime": settings.project_root / "runtime",
        "database": settings.database_path.parent,
        "jobs": settings.job_data_dir,
        "downloads": config.downloads_path,
        "screenshots": settings.screenshot_dir,
        "logs": settings.log_dir,
        "locks": config.lock_path.parent,
        "diagnostics": settings.diagnostic_directory,
        "chrome_profiles": config.profile_path.parent,
        "authentication": settings.facebook_cookie_file.parent,
    }
    problems = [
        f"{label}:{path}"
        for label, path in paths.items()
        if not path.is_dir() or not os.access(path, os.W_OK)
    ]
    if problems:
        raise RuntimeError("Missing or non-writable runtime directories: " + ", ".join(problems))
    return "Runtime directories exist and are writable", {
        "paths": {label: str(path.resolve()) for label, path in paths.items()}
    }


def _database_schema(settings: Settings) -> tuple[str, dict[str, Any]]:
    path = settings.database_path.resolve()
    if not path.is_file():
        raise RuntimeError(f"SQLite database does not exist: {path}")
    required_tables = {"jobs", "job_events", "queue", "queue_events"}
    with sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5) as connection:
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"SQLite quick_check failed: {integrity}")
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = sorted(required_tables - tables)
        if missing:
            raise RuntimeError("SQLite schema is missing tables: " + ", ".join(missing))
        connection.execute("BEGIN")
        connection.execute("CREATE TEMP TABLE preflight_rollback_probe(value INTEGER)")
        connection.rollback()
        from app.domain.enums.job_status import JobStatus
        recognized = {item.value for item in JobStatus}
        unknown: dict[str, list[str]] = {}
        for table in ("jobs", "queue"):
            values = {
                str(row[0]) for row in connection.execute(
                    f"SELECT DISTINCT status FROM {table} WHERE status IS NOT NULL"
                )
            }
            unexpected = sorted(values - recognized)
            if unexpected:
                unknown[table] = unexpected
        if unknown:
            raise RuntimeError(f"Database contains unknown job states: {unknown}")
        pending = connection.execute(
            "SELECT COUNT(*) FROM queue WHERE status IN ('CREATED','PENDING','RETRYABLE')"
        ).fetchone()[0]
    return "SQLite schema, states and rollback-only transaction are ready", {
        "path": path,
        "tables": sorted(required_tables),
        "pending_queue_items": int(pending),
    }


def _executable_version(
    executable: str | Path,
    label: str,
    *,
    timeout: float = 5,
    version_args: tuple[str, ...] = ("--version",),
) -> tuple[str, dict[str, Any]]:
    path = Path(executable) if os.sep in str(executable) else None
    resolved = str(path) if path is not None and path.is_file() else shutil.which(str(executable))
    if not resolved:
        raise RuntimeError(f"{label} executable is unavailable: {executable}")
    completed = subprocess.run(
        [resolved, *version_args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (completed.stdout or completed.stderr).strip().splitlines()
    version = output[0] if output else ""
    if completed.returncode != 0 or not version or not any(char.isdigit() for char in version):
        raise RuntimeError(f"{label} version query failed")
    return f"{label} is available", {"path": resolved, "version": version[:200]}


def _yt_dlp() -> tuple[str, dict[str, Any]]:
    import yt_dlp
    version = getattr(getattr(yt_dlp, "version", None), "__version__", "")
    if not version:
        raise RuntimeError("yt-dlp version is unavailable")
    return "yt-dlp package and configuration loader are available", {
        "version": str(version),
        "module": str(Path(yt_dlp.__file__).resolve()),
    }


def _ollama_configuration(settings: Settings) -> tuple[str, dict[str, Any]]:
    parsed = urlsplit(settings.ollama_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("OLLAMA_BASE_URL must be an HTTP(S) endpoint")
    if not settings.ollama_model:
        raise RuntimeError("OLLAMA_MODEL is required by the official pipeline")
    return "Ollama endpoint and model are configured", {
        "endpoint": f"{parsed.scheme}://{parsed.hostname}"
        + (f":{parsed.port}" if parsed.port else ""),
        "model": settings.ollama_model,
        "timeout_seconds": settings.preflight_ollama_timeout_seconds,
    }


def _canonical_paths(
    settings: Settings, config: FacebookBrowserConfig
) -> tuple[str, dict[str, Any]]:
    settings.assert_browser_config_matches(config, "preflight")
    paths = {
        "profile_path": config.profile_path,
        "lock_path": config.lock_path,
        "cookie_path": settings.facebook_cookie_file.resolve(),
    }
    if not all(path.is_absolute() for path in paths.values()):
        raise RuntimeError("Canonical preflight paths must be absolute")
    return "Worker, browser and preflight use canonical absolute paths", paths


def _cookie_check(settings: Settings) -> PreflightCheckResult:
    started = time.monotonic()
    try:
        cookie = settings.inspect_facebook_cookie()
        status = CheckStatus.PASSED
        message = "Canonical downloader cookie is ready"
        if not cookie.exists:
            status = CheckStatus.WARNING
            message = "Canonical cookie is missing; downloader is explicitly in anonymous mode"
        size = cookie.path.stat().st_size if cookie.exists else 0
        return _result(
            "facebook_cookie", "authentication", cookie.required, status, message,
            started,
            details={
                "path": cookie.path,
                "exists": cookie.exists,
                "readable": cookie.readable,
                "size_bytes": size,
                "format": cookie.authentication_method,
                "active_adapter": settings.active_reel_downloader,
                "required": cookie.required,
            },
        )
    except CookieConfigurationError as exc:
        return _result(
            "facebook_cookie", "authentication", settings.facebook_cookie_required,
            CheckStatus.FAILED, str(exc), started,
            details={"path": settings.facebook_cookie_file, "required": settings.facebook_cookie_required},
        )


def _adapter_configuration(settings: Settings) -> tuple[str, dict[str, Any]]:
    active = {
        "reel_downloader": settings.active_reel_downloader,
        "facebook_publisher": settings.active_facebook_publisher,
        "cdha": settings.active_cdha_adapter,
    }
    if any(not value for value in active.values()):
        raise RuntimeError("One or more official adapter names are empty")
    return "Official adapter configuration is explicit", active


def _composition_root(settings: Settings) -> tuple[str, dict[str, Any]]:
    from app.bootstrap import DependencyContainer
    container = DependencyContainer(settings)
    details = {
        "settings": type(container.settings).__name__,
        "repository": type(container.job_repository).__name__,
        "queue": type(container.job_queue).__name__,
        "browser": type(container.browser_manager).__name__,
        "browser_lock": type(container.browser_lock).__name__,
        "pipeline": type(container.pipeline).__name__,
        "stage_adapter": type(container.stage_adapter).__name__,
        "worker": type(container.worker).__name__,
        "reel_downloader": settings.active_reel_downloader,
        "analyzer": "OllamaAnalyzer",
        "cdha": settings.active_cdha_adapter,
        "publisher": settings.active_facebook_publisher,
    }
    settings.assert_browser_config_matches(container.browser_config, "composition root")
    return "Official dependency graph constructs without external actions", details


def _legacy_warning(settings: Settings) -> PreflightCheckResult:
    started = time.monotonic()
    warnings = settings.legacy_configuration_warnings()
    return _result(
        "legacy_paths", "migration", False,
        CheckStatus.WARNING if warnings else CheckStatus.PASSED,
        "Inactive legacy paths were detected" if warnings else "No inactive legacy paths detected",
        started,
        details={"warnings": warnings},
    )


async def _full_ollama_checks(settings: Settings) -> list[PreflightCheckResult]:
    from app.ai.provider_factory import build_analyzer
    analyzer = build_analyzer(settings, job_data_dir=settings.job_data_dir)
    timeout = settings.preflight_ollama_timeout_seconds
    checks: list[PreflightCheckResult] = []
    started = time.monotonic()
    try:
        models = await asyncio.wait_for(analyzer.list_models(), timeout=timeout)
        checks.append(_result(
            "ollama_server", "ollama", True, CheckStatus.PASSED,
            "Ollama server returned a valid model inventory", started,
            details={"endpoint": settings.ollama_base_url, "model_count": len(models)},
        ))
    except TimeoutError:
        checks.append(_result(
            "ollama_server", "ollama", True, CheckStatus.TIMEOUT,
            "Ollama server health timed out", started,
            details={"timeout_seconds": timeout},
        ))
        models = ()
    except Exception as exc:
        checks.append(_result(
            "ollama_server", "ollama", True, CheckStatus.FAILED,
            "Ollama server is unavailable", started,
            details={"error_type": type(exc).__name__, "endpoint": settings.ollama_base_url},
        ))
        models = ()

    started = time.monotonic()
    server_ready = checks[-1].status is CheckStatus.PASSED
    exact = settings.ollama_model in models
    if server_ready and exact:
        checks.append(_result(
            "ollama_model", "ollama", True, CheckStatus.PASSED,
            f"Configured Ollama model is available: {settings.ollama_model}", started,
            details={"model": settings.ollama_model, "exact_match": True},
        ))
    elif server_ready:
        checks.append(_result(
            "ollama_model", "ollama", True, CheckStatus.FAILED,
            f"Configured Ollama model is missing: {settings.ollama_model}", started,
            details={"model": settings.ollama_model, "exact_match": False},
        ))
    else:
        checks.append(_result(
            "ollama_model", "ollama", True, CheckStatus.SKIPPED,
            "Model availability could not run because the server check failed", started,
        ))

    started = time.monotonic()
    if checks[-1].status is not CheckStatus.PASSED:
        checks.append(_result(
            "ollama_inference", "ollama", True, CheckStatus.SKIPPED,
            "Minimal inference was not run because the configured model is unavailable",
            started,
        ))
        return checks
    try:
        response = await asyncio.wait_for(
            analyzer.minimal_inference("Reply with exactly: PREFLIGHT_OK"),
            timeout=timeout,
        )
        normalized = str(response).strip()
        if normalized != "PREFLIGHT_OK":
            status = CheckStatus.FAILED
            message = "Ollama minimal inference returned an invalid response"
        else:
            status = CheckStatus.PASSED
            message = "Ollama minimal non-medical inference succeeded"
        checks.append(_result(
            "ollama_inference", "ollama", True, status, message, started,
            details={
                "model": settings.ollama_model,
                "non_medical_fixture": True,
                "response_non_empty": bool(normalized),
                "response_valid": normalized == "PREFLIGHT_OK",
                "timeout_seconds": timeout,
            },
        ))
    except TimeoutError:
        checks.append(_result(
            "ollama_inference", "ollama", True, CheckStatus.TIMEOUT,
            "Ollama minimal inference timed out", started,
            details={"timeout_seconds": timeout},
        ))
    except Exception as exc:
        checks.append(_result(
            "ollama_inference", "ollama", True, CheckStatus.FAILED,
            "Ollama minimal inference failed", started,
            details={"error_type": type(exc).__name__},
        ))
    return checks


async def _diagnostic_metadata(
    settings: Settings, page: Any, check_name: str, details: dict[str, Any]
) -> tuple[str, ...]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = settings.preflight_report_dir / stamp / check_name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "diagnostic.json"
    payload = {
        "check": check_name,
        "url": safe_browser_url(str(getattr(page, "url", ""))),
        "title": "",
        "details": _sanitize(details),
    }
    try:
        payload["title"] = _sanitize(await page.title())
    except Exception:
        payload["title"] = ""
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return (str(path.resolve()),)


async def _classify_cdha_authentication(
    page: Any,
    cdha: Any,
    resolver: Any,
) -> tuple[bool, str]:
    url = str(getattr(page, "url", "")).casefold()
    title = str(await page.title()).casefold()
    if (
        any(marker in url for marker in ("/login", "/signin", "auth."))
        or any(
            marker in title
            for marker in ("đăng nhập", "login", "log in", "sign in")
        )
        or await resolver.exists(page, "cdha.login_markers", timeout_ms=800)
    ):
        return False, "LOGIN_REQUIRED"
    if await resolver.exists(page, "cdha.security_markers", timeout_ms=800):
        return False, "PERMISSION_DENIED"
    authenticated = await cdha.is_authenticated(page)
    return authenticated, "AUTHENTICATED" if authenticated else "UNKNOWN"


async def _full_browser_checks(
    settings: Settings, config: FacebookBrowserConfig
) -> list[PreflightCheckResult]:
    from app.browser.cdha_client import CDHAWebClient
    from app.browser.facebook_browser_manager import FacebookBrowserManager
    from app.browser.facebook_page_state import FacebookPageState, FacebookStateDetector
    from app.browser.selector_resolver import SelectorResolver
    from app.infrastructure.browser.file_browser_lock import FileBrowserLock

    results: list[PreflightCheckResult] = []
    lock = FileBrowserLock(
        str(config.lock_path),
        process_name="preflight",
        browser_profile=str(config.profile_path),
        browser_port=config.cdp_port,
        timeout_seconds=config.lock_timeout_seconds,
        heartbeat_seconds=config.lock_heartbeat_seconds,
    )
    started = time.monotonic()
    lock_ok = True
    if config.lock_path.exists():
        metadata = lock.read_metadata()
        if metadata is None:
            status, message = CheckStatus.FAILED, "Browser lock metadata is invalid"
        elif lock.is_lock_owner_alive(metadata):
            status, message = CheckStatus.FAILED, "BROWSER_BUSY: canonical profile has a live owner"
        elif await lock.is_lock_stale():
            status, message = CheckStatus.FAILED, "STALE_BROWSER_LOCK: manual recovery is required"
        else:
            status, message = CheckStatus.FAILED, "BROWSER_BUSY: lock ownership cannot be verified safely"
        lock_ok = False
    else:
        status, message = CheckStatus.PASSED, "Canonical browser lock is free"
    results.append(_result(
        "browser_lock", "browser", True, status, message, started,
        details={"path": config.lock_path, "profile_path": config.profile_path, "deleted": False},
    ))
    if not lock_ok:
        for name, category in (
            ("browser_start", "browser"),
            ("facebook_authentication", "facebook"),
            ("facebook_target", "facebook"),
            ("cdha_authentication", "cdha"),
            ("cdha_selectors", "cdha"),
        ):
            results.append(_result(
                name, category, True, CheckStatus.SKIPPED,
                "Check was not run because the canonical browser lock is unavailable",
                time.monotonic(),
            ))
        return results

    manager = FacebookBrowserManager(settings=settings, config=config, browser_lock=lock)
    pages: list[Any] = []
    started = time.monotonic()
    try:
        await asyncio.wait_for(
            manager.start(), timeout=settings.preflight_browser_start_timeout_seconds
        )
        results.append(_result(
            "browser_start", "browser", True, CheckStatus.PASSED,
            "Official browser manager connected with the canonical profile", started,
            details={
                "profile_path": config.profile_path,
                "cdp_endpoint": config.cdp_url,
                "timeout_seconds": settings.preflight_browser_start_timeout_seconds,
            },
        ))
    except TimeoutError:
        results.append(_result(
            "browser_start", "browser", True, CheckStatus.TIMEOUT,
            "Official browser startup timed out", started,
            details={"timeout_seconds": settings.preflight_browser_start_timeout_seconds},
        ))
    except Exception as exc:
        results.append(_result(
            "browser_start", "browser", True, CheckStatus.FAILED,
            "Official browser startup failed", started,
            details={"error_type": type(exc).__name__},
        ))

    if results[-1].status is not CheckStatus.PASSED:
        for name, category in (
            ("facebook_authentication", "facebook"),
            ("facebook_target", "facebook"),
            ("cdha_authentication", "cdha"),
            ("cdha_selectors", "cdha"),
        ):
            results.append(_result(
                name, category, True, CheckStatus.SKIPPED,
                "Check was not run because browser startup failed", time.monotonic(),
            ))
        await manager.close()
        return results

    try:
        detector = FacebookStateDetector(
            timeout_seconds=settings.preflight_facebook_timeout_seconds,
            probe_timeout_ms=settings.facebook_selector_probe_timeout_ms,
        )
        facebook_page = await manager.new_page()
        pages.append(facebook_page)
        started = time.monotonic()
        try:
            await asyncio.wait_for(
                facebook_page.goto(
                    "https://www.facebook.com/",
                    wait_until="domcontentloaded",
                    timeout=int(settings.preflight_facebook_timeout_seconds * 1000),
                ),
                timeout=settings.preflight_facebook_timeout_seconds,
            )
            detection = await detector.detect(facebook_page)
            authenticated = detection.state is FacebookPageState.LOGGED_IN
            state = detection.state.value.upper()
            artifacts: tuple[str, ...] = ()
            if not authenticated:
                artifacts = await _diagnostic_metadata(
                    settings, facebook_page, "facebook_authentication",
                    {"state": state, "probe_count": len(detection.probes)},
                )
            results.append(_result(
                "facebook_authentication", "facebook", True,
                CheckStatus.PASSED if authenticated else CheckStatus.FAILED,
                f"Facebook authentication state: {state}", started,
                details={"state": state, "url": detection.url, "title": detection.title},
                artifact_paths=artifacts,
            ))
        except TimeoutError:
            results.append(_result(
                "facebook_authentication", "facebook", True, CheckStatus.TIMEOUT,
                "Facebook authentication probe timed out", started,
                details={"timeout_seconds": settings.preflight_facebook_timeout_seconds},
            ))
        except Exception as exc:
            results.append(_result(
                "facebook_authentication", "facebook", True, CheckStatus.FAILED,
                "Facebook authentication probe failed", started,
                details={"error_type": type(exc).__name__},
            ))

        started = time.monotonic()
        if results[-1].status is not CheckStatus.PASSED:
            results.append(_result(
                "facebook_target", "facebook", True, CheckStatus.SKIPPED,
                "Target probe was not run because Facebook authentication failed", started,
            ))
        else:
            try:
                target = settings.effective_facebook_target_url()
                if not target:
                    raise RuntimeError("TARGET_NOT_CONFIGURED")
                await asyncio.wait_for(
                    facebook_page.goto(
                        target,
                        wait_until="domcontentloaded",
                        timeout=int(settings.preflight_facebook_timeout_seconds * 1000),
                    ),
                    timeout=settings.preflight_facebook_timeout_seconds,
                )
                detection = await detector.detect(facebook_page)
                actual = urlsplit(str(facebook_page.url))
                expected = urlsplit(target)
                matches = (
                    actual.netloc.casefold() == expected.netloc.casefold()
                    and (
                        actual.path.rstrip("/").casefold()
                        == expected.path.rstrip("/").casefold()
                        or expected.path.rstrip("/").casefold() == "/me"
                    )
                )
                ready = detection.state is FacebookPageState.LOGGED_IN and matches
                state = "TARGET_READY" if ready else (
                    "TARGET_MISMATCH" if not matches else "TARGET_UNAVAILABLE"
                )
                artifacts = ()
                if not ready:
                    artifacts = await _diagnostic_metadata(
                        settings, facebook_page, "facebook_target",
                        {"state": state, "expected_url": safe_browser_url(target)},
                    )
                results.append(_result(
                    "facebook_target", "facebook", True,
                    CheckStatus.PASSED if ready else CheckStatus.FAILED,
                    f"Facebook target state: {state}", started,
                    details={
                        "state": state,
                        "expected_url": safe_browser_url(target),
                        "current_url": safe_browser_url(str(facebook_page.url)),
                    },
                    artifact_paths=artifacts,
                ))
            except TimeoutError:
                results.append(_result(
                    "facebook_target", "facebook", True, CheckStatus.TIMEOUT,
                    "Facebook target probe timed out", started,
                    details={"timeout_seconds": settings.preflight_facebook_timeout_seconds},
                ))
            except Exception as exc:
                results.append(_result(
                    "facebook_target", "facebook", True, CheckStatus.FAILED,
                    f"Facebook target probe failed: {exc}", started,
                    details={"error_type": type(exc).__name__},
                ))

        cdha_page = await manager.new_page()
        pages.append(cdha_page)
        resolver = SelectorResolver(settings.selectors_path, save_html=False)
        cdha = CDHAWebClient(settings, None, manager, resolver=resolver)
        started = time.monotonic()
        try:
            await asyncio.wait_for(
                cdha_page.goto(
                    settings.cdha_url,
                    wait_until="domcontentloaded",
                    timeout=int(settings.preflight_cdha_timeout_seconds * 1000),
                ),
                timeout=settings.preflight_cdha_timeout_seconds,
            )
            authenticated, state = await asyncio.wait_for(
                _classify_cdha_authentication(cdha_page, cdha, resolver),
                timeout=settings.preflight_cdha_timeout_seconds,
            )
            artifacts = ()
            if not authenticated:
                artifacts = await _diagnostic_metadata(
                    settings, cdha_page, "cdha_authentication", {"state": state}
                )
            results.append(_result(
                "cdha_authentication", "cdha", True,
                CheckStatus.PASSED if authenticated else CheckStatus.FAILED,
                f"CDHA authentication state: {state}", started,
                details={
                    "state": state,
                    "current_url": safe_browser_url(str(cdha_page.url)),
                    "page_closed": bool(cdha_page.is_closed()),
                },
                artifact_paths=artifacts,
            ))
        except TimeoutError:
            results.append(_result(
                "cdha_authentication", "cdha", True, CheckStatus.TIMEOUT,
                "CDHA authentication probe timed out", started,
                details={"timeout_seconds": settings.preflight_cdha_timeout_seconds},
            ))
        except Exception as exc:
            results.append(_result(
                "cdha_authentication", "cdha", True, CheckStatus.FAILED,
                "CDHA authentication probe failed", started,
                details={"error_type": type(exc).__name__},
            ))

        started = time.monotonic()
        if results[-1].status is not CheckStatus.PASSED:
            results.append(_result(
                "cdha_selectors", "cdha", True, CheckStatus.SKIPPED,
                "Selector probe was not run because CDHA authentication failed", started,
            ))
        else:
            keys = (
                "cdha.authenticated_marker",
                "cdha.upload_zone",
                "cdha.upload_frame",
                "cdha.result_container",
                "cdha.analysis_complete",
            )
            found: list[str] = []
            attempted: list[str] = []
            per_key_ms = max(
                50, int(settings.preflight_selector_timeout_seconds * 1000 / len(keys))
            )
            try:
                for key in keys:
                    resolver.candidates(key)
                    attempted.append(key)
                    if await resolver.exists(cdha_page, key, timeout_ms=per_key_ms):
                        found.append(key)
                semantic = any(
                    key in found for key in (
                        "cdha.upload_zone", "cdha.upload_frame",
                        "cdha.result_container", "cdha.analysis_complete",
                    )
                )
                ready = "cdha.authenticated_marker" in found and semantic
                artifacts = ()
                if not ready:
                    artifacts = await _diagnostic_metadata(
                        settings, cdha_page, "cdha_selectors",
                        {"attempted": attempted, "found": found},
                    )
                results.append(_result(
                    "cdha_selectors", "cdha", True,
                    CheckStatus.PASSED if ready else CheckStatus.FAILED,
                    "CDHA semantic selector registry is ready"
                    if ready else "CDHA selector registry did not match required UI structure",
                    started,
                    details={
                        "attempted": attempted,
                        "found": found,
                        "current_url": safe_browser_url(str(cdha_page.url)),
                        "page_closed": bool(cdha_page.is_closed()),
                    },
                    artifact_paths=artifacts,
                ))
            except TimeoutError:
                results.append(_result(
                    "cdha_selectors", "cdha", True, CheckStatus.TIMEOUT,
                    "CDHA selector probe timed out", started,
                    details={"timeout_seconds": settings.preflight_selector_timeout_seconds},
                ))
            except Exception as exc:
                results.append(_result(
                    "cdha_selectors", "cdha", True, CheckStatus.FAILED,
                    "CDHA selector probe failed", started,
                    details={"error_type": type(exc).__name__, "attempted": attempted},
                ))
    finally:
        for page in reversed(pages):
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
        await manager.close()
    return results


def _skipped_external_checks() -> list[PreflightCheckResult]:
    return [
        PreflightCheckResult(
            name=name,
            category=category,
            required=False,
            status=CheckStatus.SKIPPED,
            message="Not executed in quick mode; run full mode for external readiness",
            duration_ms=0,
        )
        for name, category in (
            ("ollama_server", "ollama"),
            ("ollama_model", "ollama"),
            ("ollama_inference", "ollama"),
            ("browser_lock", "browser"),
            ("browser_start", "browser"),
            ("facebook_authentication", "facebook"),
            ("facebook_target", "facebook"),
            ("cdha_authentication", "cdha"),
            ("cdha_selectors", "cdha"),
        )
    ]


def _report_path(settings: Settings, mode: str, started_at: str) -> Path:
    stamp = datetime.fromisoformat(started_at).strftime("%Y%m%dT%H%M%S.%fZ")
    return (settings.preflight_report_dir / f"preflight_{mode}_{stamp}.json").resolve()


def _write_report(report: PreflightReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, ensure_ascii=False, indent=2, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def format_preflight_report(report: PreflightReport, *, verbose: bool = False) -> str:
    lines = [f"Preflight mode: {report.mode.upper()}", ""]
    labels = {
        CheckStatus.PASSED: "PASS",
        CheckStatus.WARNING: "WARN",
        CheckStatus.FAILED: "FAIL",
        CheckStatus.SKIPPED: "SKIP",
        CheckStatus.TIMEOUT: "TIMEOUT",
        CheckStatus.UNKNOWN: "UNKNOWN",
    }
    for check in report.checks:
        lines.append(f"[{labels[check.status]}] {check.name}: {check.message}")
        if verbose and check.details:
            lines.append("  " + json.dumps(check.details, ensure_ascii=False, default=str))
    lines.extend((
        "",
        f"Overall verdict: {report.overall_status}",
        f"Report: {report.report_path or '(not written)'}",
    ))
    return "\n".join(lines)


def run_preflight(
    settings: Settings,
    browser_config: FacebookBrowserConfig | None = None,
    *,
    mode: str = "quick",
    require_ollama: bool | None = None,
    write_report: bool = True,
) -> PreflightReport:
    """Run the authoritative quick or full readiness matrix.

    Quick mode is local-only. Full mode adds bounded, read-only external probes.
    ``require_ollama`` is retained only as a compatibility alias for old callers.
    """
    if require_ollama is not None:
        mode = "full" if require_ollama else "quick"
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"quick", "full"}:
        raise PreflightError(f"Unsupported preflight mode: {mode}")
    started_at = datetime.now(UTC).isoformat()
    config = browser_config or FacebookBrowserConfig.from_settings(settings)
    checks: list[PreflightCheckResult] = [
        _run_check("python_runtime", "runtime", True, _python_runtime),
        _run_check("python_packages", "runtime", True, _python_packages),
        _run_check(
            "repository_root", "runtime", True, lambda: _repository_root(settings)
        ),
        _run_check(
            "typed_settings", "configuration", True,
            lambda: _typed_settings(settings, config),
        ),
        _run_check(
            "runtime_directories", "runtime", True,
            lambda: _runtime_directories(settings, config),
        ),
        _run_check(
            "database_schema", "storage", True, lambda: _database_schema(settings)
        ),
        _run_check(
            "chrome_executable", "system_tools", True,
            lambda: _executable_version(config.executable_path, "Chrome"),
        ),
        _run_check(
            "ffmpeg", "system_tools", True,
            lambda: _executable_version(
                "ffmpeg", "FFmpeg", version_args=("-version",)
            ),
        ),
        _run_check("yt_dlp", "system_tools", True, _yt_dlp),
        _run_check(
            "ollama_configuration", "configuration", True,
            lambda: _ollama_configuration(settings),
        ),
        _run_check(
            "canonical_paths", "configuration", True,
            lambda: _canonical_paths(settings, config),
        ),
        _cookie_check(settings),
        _run_check(
            "adapter_configuration", "configuration", True,
            lambda: _adapter_configuration(settings),
        ),
    ]
    composition_prerequisites = {
        "python_packages", "typed_settings", "runtime_directories", "database_schema"
    }
    if all(
        check.status is CheckStatus.PASSED
        for check in checks
        if check.name in composition_prerequisites
    ):
        checks.append(_run_check(
            "composition_root", "configuration", True,
            lambda: _composition_root(settings),
        ))
    else:
        checks.append(PreflightCheckResult(
            "composition_root", "configuration", True, CheckStatus.SKIPPED,
            "Composition-root construction was not run because a local prerequisite failed",
            0,
        ))
    checks.append(_legacy_warning(settings))
    if normalized_mode == "full":
        checks.extend(asyncio.run(_full_ollama_checks(settings)))
        checks.extend(asyncio.run(_full_browser_checks(settings, config)))
    else:
        checks.extend(_skipped_external_checks())

    required = list(
        FULL_REQUIRED_CHECKS if normalized_mode == "full" else QUICK_REQUIRED_CHECKS
    )
    if settings.facebook_cookie_required:
        required.append("facebook_cookie")
    path = _report_path(settings, normalized_mode, started_at) if write_report else None
    if write_report:
        checks.append(PreflightCheckResult(
            "report_output", "diagnostics", True, CheckStatus.PASSED,
            "Machine-readable report path is writable", 0,
            {"path": path},
        ))
        required.append("report_output")
    report = PreflightReport.create(
        mode=normalized_mode,
        checks=checks,
        started_at=started_at,
        completed_at=datetime.now(UTC).isoformat(),
        configuration_fingerprint=settings.configuration_fingerprint(),
        required_check_names=required,
        report_path=str(path) if path else None,
    )
    if path is not None:
        try:
            _write_report(report, path)
        except Exception as exc:
            checks[-1] = PreflightCheckResult(
                "report_output", "diagnostics", True, CheckStatus.FAILED,
                f"Machine-readable report could not be written: {type(exc).__name__}",
                0,
            )
            report = PreflightReport.create(
                mode=normalized_mode,
                checks=checks,
                started_at=started_at,
                completed_at=datetime.now(UTC).isoformat(),
                configuration_fingerprint=settings.configuration_fingerprint(),
                required_check_names=required,
                report_path=None,
            )
    return report
