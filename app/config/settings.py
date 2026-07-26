from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    chrome_profile_dir: Path
    chrome_channel: str
    chrome_executable_fallback: Path
    headless: bool
    viewport_width: int
    viewport_height: int
    facebook_target_url: str
    facebook_target_type: str
    facebook_post_language: str
    facebook_final_confirmation: bool
    facebook_comment_enabled: bool
    facebook_publish_timeout_seconds: int
    facebook_upload_timeout_seconds: int
    facebook_post_discovery_timeout_seconds: int
    facebook_max_image_count: int
    facebook_max_image_size_mb: int
    facebook_allowed_image_extensions: tuple[str, ...]
    gemini_url: str
    cdha_url: str
    page_timeout_seconds: int
    upload_timeout_seconds: int
    cdha_analysis_timeout_seconds: int
    cdha_poll_interval_seconds: int
    cdha_result_stability_seconds: int
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

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        candidate = env_file or PROJECT_ROOT / ".env"
        if candidate.exists() and load_dotenv is not None:
            load_dotenv(candidate, override=False)
        return cls(
            project_root=PROJECT_ROOT,
            chrome_profile_dir=_path_env("CHROME_PROFILE_DIR", "runtime/chrome_profiles/cdha_automation"),
            chrome_channel=os.getenv("CHROME_CHANNEL", "chrome").strip() or "chrome",
            chrome_executable_fallback=_path_env("CHROME_EXECUTABLE_FALLBACK", "/usr/bin/google-chrome"),
            headless=_bool_env("HEADLESS", False),
            viewport_width=_int_env("VIEWPORT_WIDTH", 1440, 640),
            viewport_height=_int_env("VIEWPORT_HEIGHT", 1000, 480),
            facebook_target_url=os.getenv("FACEBOOK_TARGET_URL", "").strip(),
            facebook_target_type=os.getenv("FACEBOOK_TARGET_TYPE", "page").strip().lower(),
            facebook_post_language=os.getenv("FACEBOOK_POST_LANGUAGE", "vi").strip().lower(),
            facebook_final_confirmation=_bool_env("FACEBOOK_FINAL_CONFIRMATION", True),
            facebook_comment_enabled=_bool_env("FACEBOOK_COMMENT_ENABLED", True),
            facebook_publish_timeout_seconds=_int_env("FACEBOOK_PUBLISH_TIMEOUT_SECONDS", 180),
            facebook_upload_timeout_seconds=_int_env("FACEBOOK_UPLOAD_TIMEOUT_SECONDS", 180),
            facebook_post_discovery_timeout_seconds=_int_env(
                "FACEBOOK_POST_DISCOVERY_TIMEOUT_SECONDS", 120
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
            cdha_analysis_timeout_seconds=_int_env("CDHA_ANALYSIS_TIMEOUT_SECONDS", 900),
            cdha_poll_interval_seconds=_int_env("CDHA_POLL_INTERVAL_SECONDS", 3),
            cdha_result_stability_seconds=_int_env("CDHA_RESULT_STABILITY_SECONDS", 5),
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
            downloadreel_dir=_path_env("DOWNLOADREEL_DIR", "dowloadReelFB"),
            downloadreel_enable_interactions=_bool_env("DOWNLOADREEL_ENABLE_INTERACTIONS", False),
            downloadreel_cleanup_active_assets=_bool_env(
                "DOWNLOADREEL_CLEANUP_ACTIVE_ASSETS", False
            ),
            # Phase 5
            max_download_retries=_int_env("MAX_DOWNLOAD_RETRIES", 2),
            max_gemini_retries=_int_env("MAX_GEMINI_RETRIES", 2),
            max_cdha_retries=_int_env("MAX_CDHA_RETRIES", 2),
            max_facebook_prepare_retries=_int_env("MAX_FACEBOOK_PREPARE_RETRIES", 2),
            max_permalink_retries=_int_env("MAX_PERMALINK_RETRIES", 3),
            max_comment_retries=_int_env("MAX_COMMENT_RETRIES", 2),
            retry_initial_delay_seconds=_float_env("RETRY_INITIAL_DELAY_SECONDS", 0.5, 0.0),
            retry_multiplier=_float_env("RETRY_MULTIPLIER", 2.0, 1.0),
            retry_max_delay_seconds=_float_env("RETRY_MAX_DELAY_SECONDS", 8.0, 0.0),
            retry_jitter_seconds=_float_env("RETRY_JITTER_SECONDS", 0.25, 0.0),
            save_diagnostic_html=_bool_env("SAVE_DIAGNOSTIC_HTML", True),
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
        )

    def ensure_runtime_directories(self) -> None:
        for path in (
            self.chrome_profile_dir,
            self.database_path.parent,
            self.job_data_dir,
            self.log_dir,
            self.screenshot_dir,
            self.diagnostic_directory,
        ):
            path.mkdir(parents=True, exist_ok=True)
