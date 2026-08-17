from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    value = float(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value

def _path_env(name: str, default: str) -> Path:
    path = Path(os.path.expanduser(os.getenv(name, default)))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()



class CookieConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CookieFileInspection:
    path: Path
    exists: bool
    readable: bool
    valid: bool
    required: bool
    status: str
    authentication_method: str


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    application_environment: str
    chrome_profile_dir: Path
    chrome_channel: str
    chrome_executable_fallback: Path
    headless: bool
    viewport_width: int
    viewport_height: int
    facebook_target_url: str
    facebook_target_type: str
    facebook_post_language: str
    facebook_post_hashtags: tuple[str, ...]
    facebook_final_confirmation: bool
    facebook_comment_enabled: bool
    facebook_publish_timeout_seconds: int
    facebook_publish_settle_timeout_seconds: float
    facebook_publish_poll_interval_seconds: float
    facebook_upload_timeout_seconds: int
    facebook_post_discovery_timeout_seconds: int
    facebook_reconciliation_reload_initial_seconds: float
    facebook_reconciliation_reload_max_seconds: float
    facebook_max_image_count: int
    facebook_max_image_size_mb: int
    facebook_allowed_image_extensions: tuple[str, ...]
    gemini_url: str
    cdha_url: str
    page_timeout_seconds: int
    upload_timeout_seconds: int
    browser_action_timeout_seconds: float
    browser_navigation_timeout_seconds: float
    cdha_upload_timeout_seconds: float
    cdha_analysis_timeout_seconds: int
    cdha_result_timeout_seconds: float
    cdha_poll_interval_seconds: int
    cdha_result_stability_seconds: int
    cdha_large_file_threshold_mb: float
    clinical_factors_max_chars: int
    clinical_factors_comment_max_chars: int
    clinical_factors_max_comments: int
    gemini_comment_total_max_chars: int
    gemini_prompt_max_chars: int
    database_path: Path
    job_data_dir: Path
    log_dir: Path
    screenshot_dir: Path
    selectors_path: Path
    log_level: str
    log_max_bytes: int
    log_backup_count: int
    downloadreel_dir: Path
    downloadreel_enable_interactions: bool
    downloadreel_cleanup_active_assets: bool
    # Phase 5
    max_download_retries: int
    max_gemini_retries: int
    max_cdha_retries: int
    max_facebook_prepare_retries: int
    max_facebook_reconciliation_retries: int
    max_permalink_retries: int
    max_comment_retries: int
    retry_initial_delay_seconds: float
    retry_multiplier: float
    retry_max_delay_seconds: float
    retry_jitter_seconds: float
    save_diagnostic_html: bool
    save_raw_gemini_prompt: bool
    save_raw_gemini_response: bool
    diagnostic_directory: Path
    test_mode: bool
    facebook_test_target_url: str
    allow_production_target_in_test_mode: bool
    test_mode_disable_comment: bool
    # Ollama / local AI engine
    ollama_base_url: str
    ollama_model: str
    ollama_timeout_seconds: int
    ollama_keep_alive: str
    ollama_temperature: float
    ollama_top_p: float
    ollama_repeat_penalty: float
    ollama_num_predict: int
    ollama_max_retries: int
    ollama_prompt_version: str
    ollama_prompt_max_chars: int
    ollama_comment_total_max_chars: int
    save_raw_ollama_response: bool
    # Frame extraction
    frame_extraction_enabled: bool
    frame_extraction_interval_seconds: int
    frame_extraction_max_frames: int
    frame_extraction_width: int
    frame_jpeg_quality: int
    frame_similarity_threshold: float
    auto_approve_review: bool
    browser_lock_timeout_seconds: float
    browser_lock_heartbeat_seconds: float
    browser_lock_wait_timeout_seconds: float
    browser_lock_retry_interval_seconds: float
    browser_cdp_host: str
    browser_cdp_port: int
    browser_lock_path: Path
    browser_pid_path: Path
    browser_download_dir: Path
    facebook_cookie_file: Path
    facebook_cookie_required: bool
    active_reel_downloader: str
    active_facebook_publisher: str
    active_cdha_adapter: str
    browser_startup_timeout_seconds: float
    browser_max_start_attempts: int
    worker_poll_interval_seconds: float
    worker_stage_timeout_seconds: float
    job_lease_seconds: float
    job_heartbeat_seconds: float
    facebook_state_detection_timeout_seconds: float
    facebook_selector_probe_timeout_ms: int
    facebook_navigation_timeout_ms: int
    facebook_max_retries: int
    facebook_save_debug_artifacts: bool
    facebook_manual_auth_timeout_seconds: int
    preflight_ollama_timeout_seconds: float
    preflight_browser_start_timeout_seconds: float
    preflight_facebook_timeout_seconds: float
    preflight_cdha_timeout_seconds: float
    preflight_selector_timeout_seconds: float
    preflight_report_dir: Path

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        candidate = env_file or PROJECT_ROOT / ".env"
        dotenv_added_keys: set[str] = set()
        if candidate.exists() and load_dotenv is not None:
            keys_before_dotenv = set(os.environ)
            load_dotenv(candidate, override=False)
            dotenv_added_keys = set(os.environ) - keys_before_dotenv
        settings = cls(
            project_root=PROJECT_ROOT,
            application_environment=os.getenv("APP_ENV", "development").strip() or "development",
            chrome_profile_dir=_path_env("CHROME_PROFILE_DIR", "runtime/chrome_profiles/cdha_automation"),
            chrome_channel=os.getenv("CHROME_CHANNEL", "chrome").strip() or "chrome",
            chrome_executable_fallback=_path_env("CHROME_EXECUTABLE_FALLBACK", "/usr/bin/google-chrome"),
            headless=_bool_env("HEADLESS", False),
            viewport_width=_int_env("VIEWPORT_WIDTH", 1440, 640),
            viewport_height=_int_env("VIEWPORT_HEIGHT", 1000, 480),
            facebook_target_url=os.getenv("FACEBOOK_TARGET_URL", "").strip(),
            facebook_target_type=os.getenv("FACEBOOK_TARGET_TYPE", "page").strip().lower(),
            facebook_post_language=os.getenv("FACEBOOK_POST_LANGUAGE", "vi").strip().lower(),
            facebook_post_hashtags=tuple(
                item.strip()
                for item in os.getenv(
                    "FACEBOOK_POST_HASHTAGS",
                    "#CDHA #SieuAm #ChanDoanHinhAnh #MedicalAI #HoiChan",
                ).replace(",", " ").split()
                if item.strip()
            ),
            facebook_final_confirmation=_bool_env("FACEBOOK_FINAL_CONFIRMATION", True),
            facebook_comment_enabled=_bool_env("FACEBOOK_COMMENT_ENABLED", True),
            facebook_publish_timeout_seconds=_int_env("FACEBOOK_PUBLISH_TIMEOUT_SECONDS", 180),
            facebook_publish_settle_timeout_seconds=_float_env(
                "FACEBOOK_PUBLISH_SETTLE_TIMEOUT_SECONDS", 30, 0.1
            ),
            facebook_publish_poll_interval_seconds=_float_env(
                "FACEBOOK_PUBLISH_POLL_INTERVAL_SECONDS", 0.5, 0.001
            ),
            facebook_upload_timeout_seconds=_int_env("FACEBOOK_UPLOAD_TIMEOUT_SECONDS", 180),
            facebook_post_discovery_timeout_seconds=_int_env(
                "FACEBOOK_POST_DISCOVERY_TIMEOUT_SECONDS", 120
            ),
            facebook_reconciliation_reload_initial_seconds=_float_env(
                "FACEBOOK_RECONCILIATION_RELOAD_INITIAL_SECONDS", 2, 0.01
            ),
            facebook_reconciliation_reload_max_seconds=_float_env(
                "FACEBOOK_RECONCILIATION_RELOAD_MAX_SECONDS", 15, 0.01
            ),
            facebook_max_image_count=_int_env("FACEBOOK_MAX_IMAGE_COUNT", 10),
            facebook_max_image_size_mb=_int_env("FACEBOOK_MAX_IMAGE_SIZE_MB", 20),
            facebook_allowed_image_extensions=tuple(
                item.strip().lower()
                for item in os.getenv(
                    "FACEBOOK_ALLOWED_IMAGE_EXTENSIONS", ".png,.jpg,.jpeg,.webp"
                ).split(",")
                if item.strip()
            ),
            gemini_url=os.getenv("GEMINI_URL", "https://gemini.google.com/app").strip(),
            cdha_url=os.getenv(
                "CDHA_URL", "https://cdha.ai/dash?modality=us_video&country=VN"
            ).strip(),
            page_timeout_seconds=_int_env("PAGE_TIMEOUT_SECONDS", 60),
            upload_timeout_seconds=_int_env("UPLOAD_TIMEOUT_SECONDS", 180),
            browser_action_timeout_seconds=_float_env(
                "BROWSER_ACTION_TIMEOUT_SECONDS", 60, 0.001
            ),
            browser_navigation_timeout_seconds=_float_env(
                "BROWSER_NAVIGATION_TIMEOUT_SECONDS", 60, 0.001
            ),
            cdha_upload_timeout_seconds=_float_env(
                "CDHA_UPLOAD_TIMEOUT_SECONDS", 180, 0.001
            ),
            cdha_analysis_timeout_seconds=_int_env("CDHA_ANALYSIS_TIMEOUT_SECONDS", 900),
            cdha_result_timeout_seconds=_float_env(
                "CDHA_RESULT_TIMEOUT_SECONDS", 120, 0.001
            ),
            cdha_poll_interval_seconds=_int_env("CDHA_POLL_INTERVAL_SECONDS", 3),
            cdha_result_stability_seconds=_int_env("CDHA_RESULT_STABILITY_SECONDS", 5),
            cdha_large_file_threshold_mb=_float_env(
                "CDHA_LARGE_FILE_THRESHOLD_MB", 50, 0.001
            ),
            clinical_factors_max_chars=_int_env(
                "MAX_CLINICAL_FACTORS_CHARACTERS",
                _int_env("CLINICAL_FACTORS_MAX_CHARS", 5000, 500),
                500,
            ),
            clinical_factors_comment_max_chars=_int_env(
                "CLINICAL_FACTORS_COMMENT_MAX_CHARS", 600, 50
            ),
            clinical_factors_max_comments=_int_env(
                "MAX_GEMINI_COMMENT_COUNT",
                _int_env("CLINICAL_FACTORS_MAX_COMMENTS", 100),
            ),
            gemini_comment_total_max_chars=_int_env(
                "MAX_GEMINI_COMMENT_CHARACTERS", 15_000
            ),
            gemini_prompt_max_chars=_int_env("MAX_GEMINI_PROMPT_CHARACTERS", 30_000),
            database_path=_path_env("DATABASE_PATH", "data/jobs.sqlite3"),
            job_data_dir=_path_env("JOB_DATA_DIR", "data/jobs"),
            log_dir=_path_env("LOG_DIR", "logs"),
            screenshot_dir=_path_env("SCREENSHOT_DIR", "screenshots"),
            selectors_path=PROJECT_ROOT / "app" / "config" / "selectors.yaml",
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_max_bytes=_int_env("LOG_MAX_BYTES", 5 * 1024 * 1024, 1024),
            log_backup_count=_int_env("LOG_BACKUP_COUNT", 5, 1),
            downloadreel_dir=_path_env("DOWNLOADREEL_DIR", "app/infrastructure/legacy/dowloadReelFB"),
            downloadreel_enable_interactions=_bool_env("DOWNLOADREEL_ENABLE_INTERACTIONS", False),
            downloadreel_cleanup_active_assets=_bool_env(
                "DOWNLOADREEL_CLEANUP_ACTIVE_ASSETS", False
            ),
            # Phase 5
            max_download_retries=_int_env("MAX_DOWNLOAD_RETRIES", 2),
            max_gemini_retries=_int_env("MAX_GEMINI_RETRIES", 2),
            max_cdha_retries=_int_env("MAX_CDHA_RETRIES", 2),
            max_facebook_prepare_retries=_int_env("MAX_FACEBOOK_PREPARE_RETRIES", 2),
            max_facebook_reconciliation_retries=_int_env(
                "MAX_FACEBOOK_RECONCILIATION_RETRIES", 3, 1
            ),
            max_permalink_retries=_int_env("MAX_PERMALINK_RETRIES", 3),
            max_comment_retries=_int_env("MAX_COMMENT_RETRIES", 2),
            retry_initial_delay_seconds=_float_env("RETRY_INITIAL_DELAY_SECONDS", 0.5, 0.0),
            retry_multiplier=_float_env("RETRY_MULTIPLIER", 2.0, 1.0),
            retry_max_delay_seconds=_float_env("RETRY_MAX_DELAY_SECONDS", 8.0, 0.0),
            retry_jitter_seconds=_float_env("RETRY_JITTER_SECONDS", 0.25, 0.0),
            save_diagnostic_html=_bool_env("SAVE_DIAGNOSTIC_HTML", False),
            save_raw_gemini_prompt=_bool_env("SAVE_RAW_GEMINI_PROMPT", False),
            save_raw_gemini_response=_bool_env("SAVE_RAW_GEMINI_RESPONSE", False),
            diagnostic_directory=_path_env("DIAGNOSTIC_DIRECTORY", "data/diagnostics"),
            test_mode=_bool_env("TEST_MODE", False),
            facebook_test_target_url=os.getenv("FACEBOOK_TEST_TARGET_URL", "").strip(),
            allow_production_target_in_test_mode=_bool_env(
                "ALLOW_PRODUCTION_TARGET_IN_TEST_MODE", False
            ),
            test_mode_disable_comment=_bool_env("TEST_MODE_DISABLE_COMMENT", True),
            # Ollama / local AI engine
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip(),
            ollama_model=os.getenv("OLLAMA_MODEL", "").strip(),
            ollama_timeout_seconds=_int_env("OLLAMA_TIMEOUT_SECONDS", 300),
            ollama_keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "10m").strip(),
            ollama_temperature=_float_env("OLLAMA_TEMPERATURE", 0.1, 0.0),
            ollama_top_p=_float_env("OLLAMA_TOP_P", 0.8, 0.0),
            ollama_repeat_penalty=_float_env("OLLAMA_REPEAT_PENALTY", 1.2, 0.0),
            ollama_num_predict=_int_env("OLLAMA_NUM_PREDICT", 250, 1),
            ollama_max_retries=_int_env("OLLAMA_MAX_RETRIES", 2),
            ollama_prompt_version=os.getenv("OLLAMA_PROMPT_VERSION", "ollama-clinical-v1").strip(),
            ollama_prompt_max_chars=_int_env("OLLAMA_PROMPT_MAX_CHARS", 30_000),
            ollama_comment_total_max_chars=_int_env("OLLAMA_COMMENT_TOTAL_MAX_CHARS", 15_000),
            save_raw_ollama_response=_bool_env("SAVE_RAW_OLLAMA_RESPONSE", False),
            # Frame extraction
            frame_extraction_enabled=_bool_env("FRAME_EXTRACTION_ENABLED", True),
            frame_extraction_interval_seconds=_int_env("FRAME_EXTRACTION_INTERVAL_SECONDS", 2),
            frame_extraction_max_frames=_int_env("FRAME_EXTRACTION_MAX_FRAMES", 12),
            frame_extraction_width=_int_env("FRAME_EXTRACTION_WIDTH", 1024),
            frame_jpeg_quality=_int_env("FRAME_JPEG_QUALITY", 85),
            frame_similarity_threshold=_float_env("FRAME_SIMILARITY_THRESHOLD", 5.0, 0.0),
            auto_approve_review=_bool_env("AUTO_APPROVE_REVIEW", False),
            browser_lock_timeout_seconds=_float_env("BROWSER_LOCK_TIMEOUT_SECONDS", 120, 1),
            browser_lock_heartbeat_seconds=_float_env("BROWSER_LOCK_HEARTBEAT_SECONDS", 15, 0.1),
            browser_lock_wait_timeout_seconds=_float_env("BROWSER_LOCK_WAIT_TIMEOUT_SECONDS", 180, 0),
            browser_lock_retry_interval_seconds=_float_env("BROWSER_LOCK_RETRY_INTERVAL_SECONDS", 5, 0.01),
            browser_cdp_host=os.getenv("FACEBOOK_CDP_HOST", "127.0.0.1").strip(),
            browser_cdp_port=_int_env("FACEBOOK_CDP_PORT", 9222, 1),
            browser_lock_path=_path_env(
                "FACEBOOK_BROWSER_LOCK_PATH", "runtime/locks/facebook_browser.lock"
            ),
            browser_pid_path=_path_env(
                "FACEBOOK_BROWSER_PID_PATH", "runtime/pids/facebook_chrome.pid"
            ),
            browser_download_dir=_path_env(
                "FACEBOOK_DOWNLOAD_PATH", "runtime/downloads/facebook"
            ),
            facebook_cookie_file=_path_env(
                "FACEBOOK_COOKIE_FILE", "runtime/auth/facebook_cookies.txt"
            ),
            facebook_cookie_required=_bool_env("FACEBOOK_COOKIE_REQUIRED", False),
            active_reel_downloader="DownloadReelAdapter",
            active_facebook_publisher="FacebookPublisherAdapter",
            active_cdha_adapter="CDHAWebClient",
            browser_startup_timeout_seconds=_float_env(
                "FACEBOOK_STARTUP_TIMEOUT_SECONDS", 30, 1
            ),
            browser_max_start_attempts=_int_env("FACEBOOK_MAX_START_ATTEMPTS", 2, 1),
            worker_poll_interval_seconds=_float_env("WORKER_POLL_INTERVAL_SECONDS", 1, 0.05),
            worker_stage_timeout_seconds=_float_env(
                "WORKER_STAGE_TIMEOUT_SECONDS", 1200, 0.001
            ),
            job_lease_seconds=_float_env("JOB_LEASE_SECONDS", 240, 3),
            job_heartbeat_seconds=_float_env("JOB_HEARTBEAT_SECONDS", 30, 1),
            facebook_state_detection_timeout_seconds=_float_env("FACEBOOK_STATE_DETECTION_TIMEOUT_SECONDS", 15, 0.1),
            facebook_selector_probe_timeout_ms=_int_env("FACEBOOK_SELECTOR_PROBE_TIMEOUT_MS", 1000, 10),
            facebook_navigation_timeout_ms=_int_env("FACEBOOK_NAVIGATION_TIMEOUT_MS", 45000, 1000),
            facebook_max_retries=_int_env("FACEBOOK_MAX_RETRIES", 3, 0),
            facebook_save_debug_artifacts=_bool_env("FACEBOOK_SAVE_DEBUG_ARTIFACTS", True),
            facebook_manual_auth_timeout_seconds=_int_env("FACEBOOK_MANUAL_AUTH_TIMEOUT_SECONDS", 900, 1),
            preflight_ollama_timeout_seconds=_float_env(
                "PREFLIGHT_OLLAMA_TIMEOUT_SECONDS", 30, 0.1
            ),
            preflight_browser_start_timeout_seconds=_float_env(
                "PREFLIGHT_BROWSER_START_TIMEOUT_SECONDS", 45, 0.1
            ),
            preflight_facebook_timeout_seconds=_float_env(
                "PREFLIGHT_FACEBOOK_TIMEOUT_SECONDS", 30, 0.1
            ),
            preflight_cdha_timeout_seconds=_float_env(
                "PREFLIGHT_CDHA_TIMEOUT_SECONDS", 30, 0.1
            ),
            preflight_selector_timeout_seconds=_float_env(
                "PREFLIGHT_SELECTOR_TIMEOUT_SECONDS", 5, 0.1
            ),
            preflight_report_dir=_path_env(
                "PREFLIGHT_REPORT_DIR", "runtime/diagnostics/preflight"
            ),
        )
        settings._validate_compatibility_aliases()
        for name in dotenv_added_keys:
            os.environ.pop(name, None)
        return settings

    @staticmethod
    def _normalized_env_path(value: str) -> Path:
        candidate = Path(os.path.expanduser(value))
        return (candidate if candidate.is_absolute() else PROJECT_ROOT / candidate).resolve()

    def _validate_compatibility_aliases(self) -> None:
        conflicts: list[str] = []
        path_aliases = (
            ("FACEBOOK_PROFILE_PATH", "CHROME_PROFILE_DIR", self.chrome_profile_dir),
            ("FB_POSTER_PROFILE", "CHROME_PROFILE_DIR", self.chrome_profile_dir),
            ("FACEBOOK_CHROME_EXECUTABLE", "CHROME_EXECUTABLE_FALLBACK", self.chrome_executable_fallback),
            ("FACEBOOK_QUEUE_DATABASE_PATH", "DATABASE_PATH", self.database_path),
        )
        for alias, canonical, expected in path_aliases:
            raw = os.getenv(alias)
            canonical_raw = os.getenv(canonical)
            if raw and canonical_raw and self._normalized_env_path(raw) != expected:
                conflicts.append(f"{canonical} conflicts with legacy {alias}")
        raw_headless = os.getenv("FACEBOOK_BROWSER_HEADLESS")
        if raw_headless is not None and os.getenv("HEADLESS") is not None:
            alias_headless = raw_headless.strip().lower() in {"1", "true", "yes", "y", "on"}
            if alias_headless != self.headless:
                conflicts.append("HEADLESS conflicts with legacy FACEBOOK_BROWSER_HEADLESS")
        if conflicts:
            raise ValueError("Conflicting browser configuration: " + "; ".join(conflicts))

    def inspect_facebook_cookie(self) -> CookieFileInspection:
        path = Path(self.facebook_cookie_file)
        if not path.is_absolute():
            path = (self.project_root / path).resolve()
        else:
            path = path.resolve()
        if not path.exists():
            if self.facebook_cookie_required:
                raise CookieConfigurationError(f"Required Facebook cookie file is missing: {path}")
            return CookieFileInspection(
                path, False, False, False, False, "missing_optional", "anonymous"
            )
        if not path.is_file() or not (path.stat().st_mode & 0o444):
            raise CookieConfigurationError(f"Facebook cookie file is unreadable: {path}")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CookieConfigurationError(
                f"Facebook cookie file is unreadable: {path} ({type(exc).__name__})"
            ) from exc
        lines = [line for line in content.splitlines() if line.strip()]
        header_ok = bool(lines) and lines[0].lstrip().startswith(
            ("# Netscape HTTP Cookie File", "# HTTP Cookie File")
        )
        row_ok = any(
            len(line.split("\t")) >= 7
            for line in lines[1:]
            if not line.lstrip().startswith("#")
        )
        if not (header_ok and row_ok):
            raise CookieConfigurationError(
                f"Facebook cookie file has invalid Netscape format: {path}"
            )
        return CookieFileInspection(
            path, True, True, True, self.facebook_cookie_required,
            "ready", "netscape_cookie_file"
        )

    @staticmethod
    def _sanitized_url(value: str) -> str:
        if not value:
            return ""
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def legacy_configuration_warnings(self) -> list[str]:
        warnings: list[str] = []
        legacy_paths = (
            self.project_root / "runtime/chrome_profiles/facebook",
            self.project_root / "Cookie.txt",
            self.project_root / "runtime/cookies.txt",
            self.project_root / "app/infrastructure/legacy/dowloadReelFB/cookies.txt",
        )
        for path in legacy_paths:
            resolved = path.resolve()
            if resolved.exists() and resolved not in {
                self.chrome_profile_dir.resolve(), self.facebook_cookie_file.resolve()
            }:
                warnings.append(
                    f"Legacy path retained but inactive: {resolved}. Migrate manually; no data was moved."
                )
        return warnings

    def sanitized_runtime_configuration(self) -> dict[str, Any]:
        try:
            cookie = self.inspect_facebook_cookie()
            cookie_status = cookie.status
            cookie_exists = cookie.exists
        except CookieConfigurationError:
            cookie_status = "invalid_or_unreadable"
            cookie_exists = self.facebook_cookie_file.exists()
        return {
            "application_environment": self.application_environment,
            "project_root": str(self.project_root),
            "browser": {
                "executable": str(self.chrome_executable_fallback),
                "profile_path": str(self.chrome_profile_dir),
                "headless": self.headless,
                "cdp_host": self.browser_cdp_host,
                "cdp_port": self.browser_cdp_port,
                "startup_strategy": "managed_chrome_cdp",
                "startup_timeout_seconds": self.browser_startup_timeout_seconds,
                "action_timeout_seconds": self.browser_action_timeout_seconds,
                "navigation_timeout_seconds": self.browser_navigation_timeout_seconds,
                "lock_path": str(self.browser_lock_path),
                "pid_path": str(self.browser_pid_path),
            },
            "authentication": {
                "facebook_reel": {
                    "method": "netscape_cookie_file_or_anonymous",
                    "cookie_file": str(self.facebook_cookie_file),
                    "cookie_exists": cookie_exists,
                    "cookie_status": cookie_status,
                },
                "facebook_publisher": {"method": "persistent_chrome_profile"},
                "cdha": {"method": "persistent_chrome_profile"},
            },
            "urls": {
                "facebook_target": self._sanitized_url(self.effective_facebook_target_url())
                if (self.facebook_target_url or self.facebook_test_target_url) else "",
                "cdha": self._sanitized_url(self.cdha_url),
            },
            "active_adapters": {
                "reel_downloader": self.active_reel_downloader,
                "facebook_publisher": self.active_facebook_publisher,
                "cdha": self.active_cdha_adapter,
            },
            "timeouts": {
                "browser_action_seconds": self.browser_action_timeout_seconds,
                "browser_navigation_seconds": self.browser_navigation_timeout_seconds,
                "cdha_upload_seconds": self.cdha_upload_timeout_seconds,
                "cdha_analysis_seconds": self.cdha_analysis_timeout_seconds,
                "cdha_result_seconds": self.cdha_result_timeout_seconds,
                "facebook_publish_seconds": self.facebook_publish_timeout_seconds,
                "facebook_publish_settle_seconds": self.facebook_publish_settle_timeout_seconds,
                "facebook_publish_poll_seconds": self.facebook_publish_poll_interval_seconds,
                "facebook_post_discovery_seconds": self.facebook_post_discovery_timeout_seconds,
                "facebook_reconciliation_reload_initial_seconds": self.facebook_reconciliation_reload_initial_seconds,
                "facebook_reconciliation_reload_max_seconds": self.facebook_reconciliation_reload_max_seconds,
                "facebook_reconciliation_max_attempts": self.max_facebook_reconciliation_retries,
                "queue_lease_seconds": self.job_lease_seconds,
                "worker_heartbeat_seconds": self.job_heartbeat_seconds,
                "worker_stage_seconds": self.worker_stage_timeout_seconds,
            },
            "runtime_directories": {
                "downloads": str(self.browser_download_dir),
                "diagnostics": str(self.diagnostic_directory),
                "preflight_reports": str(self.preflight_report_dir),
                "screenshots": str(self.screenshot_dir),
                "logs": str(self.log_dir),
            },
            "legacy_warnings": self.legacy_configuration_warnings(),
        }

    def configuration_fingerprint(self) -> str:
        payload = json.dumps(
            self.sanitized_runtime_configuration(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def assert_browser_config_matches(self, config: Any, component: str) -> None:
        checks = {
            "profile_path": self.chrome_profile_dir,
            "lock_path": self.browser_lock_path,
            "executable_path": self.chrome_executable_fallback,
            "cdp_port": self.browser_cdp_port,
        }
        mismatches = [
            name for name, expected in checks.items()
            if getattr(config, name) != expected
        ]
        if mismatches:
            raise ValueError(
                f"{component} browser configuration mismatch: {', '.join(mismatches)}"
            )


    def validate(self) -> None:
        missing = []
        if not self.facebook_target_url and not self.test_mode:
            missing.append("FACEBOOK_TARGET_URL is required in non-test mode.")
        if not self.cdha_url:
            missing.append("CDHA_URL is required.")
        if self.job_heartbeat_seconds >= self.job_lease_seconds:
            missing.append("JOB_HEARTBEAT_SECONDS must be less than JOB_LEASE_SECONDS.")
        positive_timeouts = {
            "BROWSER_ACTION_TIMEOUT_SECONDS": self.browser_action_timeout_seconds,
            "BROWSER_NAVIGATION_TIMEOUT_SECONDS": self.browser_navigation_timeout_seconds,
            "CDHA_UPLOAD_TIMEOUT_SECONDS": self.cdha_upload_timeout_seconds,
            "CDHA_ANALYSIS_TIMEOUT_SECONDS": self.cdha_analysis_timeout_seconds,
            "CDHA_RESULT_TIMEOUT_SECONDS": self.cdha_result_timeout_seconds,
            "QUEUE_LEASE_SECONDS": self.job_lease_seconds,
            "WORKER_HEARTBEAT_INTERVAL_SECONDS": self.job_heartbeat_seconds,
            "WORKER_STAGE_TIMEOUT_SECONDS": self.worker_stage_timeout_seconds,
        }
        missing.extend(
            f"{name} must be greater than zero."
            for name, value in positive_timeouts.items()
            if value <= 0
        )
        try:
            self.effective_facebook_target_url()
        except ValueError as exc:
            missing.append(str(exc))

        if missing:
            raise ValueError(f"Invalid Configuration: Missing or invalid settings: {', '.join(missing)}")

    def effective_facebook_target_url(self) -> str:
        if not self.test_mode:
            return self.facebook_target_url.strip()
        target = self.facebook_test_target_url.strip()
        if not target:
            raise ValueError("FACEBOOK_TEST_TARGET_URL is required when TEST_MODE=true.")
        production = self.facebook_target_url.strip()
        if (
            production
            and target.rstrip("/").casefold() == production.rstrip("/").casefold()
            and not self.allow_production_target_in_test_mode
        ):
            raise ValueError(
                "Test target matches FACEBOOK_TARGET_URL; set a separate target or explicitly enable ALLOW_PRODUCTION_TARGET_IN_TEST_MODE."
            )
        return target

    def ensure_runtime_directories(self) -> None:
        for path in (
            self.chrome_profile_dir,
            self.database_path.parent,
            self.job_data_dir,
            self.log_dir,
            self.screenshot_dir,
            self.diagnostic_directory,
            self.browser_lock_path.parent,
            self.browser_pid_path.parent,
            self.browser_download_dir,
            self.facebook_cookie_file.parent,
            self.preflight_report_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
