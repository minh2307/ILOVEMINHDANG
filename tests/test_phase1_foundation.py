from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from app.browser.chrome_manager import ChromeManager, ProfileInUseError
from app.browser.selector_resolver import SelectorResolver
from app.config.settings import Settings
from app.config.facebook_browser import FacebookBrowserConfig
from app.logging_setup import StructuredJsonFormatter


def test_settings_use_dedicated_profile_and_installed_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHROME_PROFILE_DIR", raising=False)
    monkeypatch.delenv("CHROME_CHANNEL", raising=False)
    settings = Settings.from_env(env_file=Path("/nonexistent-cdha-env"))

    assert settings.chrome_channel == "chrome"
    assert settings.chrome_profile_dir.name == "cdha_automation"
    assert settings.chrome_profile_dir.as_posix().endswith(
        "runtime/chrome_profiles/cdha_automation"
    )


def test_settings_reject_heartbeat_that_cannot_renew_lease() -> None:
    settings = replace(
        Settings.from_env(env_file=Path("/nonexistent-cdha-env")),
        facebook_target_url="https://www.facebook.com/example",
        job_lease_seconds=30,
        job_heartbeat_seconds=30,
    )

    with pytest.raises(ValueError, match="JOB_HEARTBEAT_SECONDS"):
        settings.validate()


def test_test_mode_uses_separate_facebook_target_and_configured_hashtags() -> None:
    settings = replace(
        Settings.from_env(env_file=Path("/nonexistent-cdha-env")),
        test_mode=True,
        facebook_target_url="https://www.facebook.com/production.page",
        facebook_test_target_url="https://www.facebook.com/e2e.page",
        facebook_post_hashtags=("#CDHA", "#E2E"),
    )

    settings.validate()

    assert settings.effective_facebook_target_url() == "https://www.facebook.com/e2e.page"
    assert settings.facebook_post_hashtags == ("#CDHA", "#E2E")


def test_test_mode_rejects_production_target_without_explicit_override() -> None:
    settings = replace(
        Settings.from_env(env_file=Path("/nonexistent-cdha-env")),
        test_mode=True,
        facebook_target_url="https://www.facebook.com/same.page/",
        facebook_test_target_url="https://www.facebook.com/same.page",
        allow_production_target_in_test_mode=False,
    )

    with pytest.raises(ValueError, match="Test target matches"):
        settings.validate()


def test_official_browser_config_is_derived_from_typed_settings(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(env_file=Path("/nonexistent-cdha-env")),
        database_path=tmp_path / "application.sqlite3",
        chrome_profile_dir=tmp_path / "profile",
        browser_lock_path=tmp_path / "locks" / "browser.lock",
        browser_pid_path=tmp_path / "pids" / "browser.pid",
        browser_cdp_port=9333,
    )

    config = FacebookBrowserConfig.from_settings(settings)

    assert config.queue_database_path == settings.database_path
    assert config.profile_path == settings.chrome_profile_dir
    assert config.lock_path == settings.browser_lock_path
    assert config.pid_path == settings.browser_pid_path
    assert config.cdp_url == "http://127.0.0.1:9333"


def test_selector_configuration_loads_all_services() -> None:
    settings = Settings.from_env(env_file=Path("/nonexistent-cdha-env"))
    resolver = SelectorResolver(settings.selectors_path)

    assert resolver.candidates("gemini.prompt_input")
    assert resolver.candidates("cdha.video_input")
    assert resolver.candidates("facebook.post_button")
    with pytest.raises(KeyError):
        resolver.candidates("missing.selector")


@pytest.mark.asyncio
async def test_profile_lock_rejects_second_manager(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(env_file=Path("/nonexistent-cdha-env")),
        chrome_profile_dir=tmp_path / "profile",
        browser_lock_path=tmp_path / "locks" / "browser.lock",
    )
    first = ChromeManager(settings)
    second = ChromeManager(settings)

    assert await first.browser_lock.acquire("first-manager")
    try:
        assert not await second.browser_lock.acquire("second-manager")
        metadata = first.browser_lock.read_metadata()
        assert metadata is not None
        assert metadata["browser_profile"] == str(settings.chrome_profile_dir.resolve())
    finally:
        await first.browser_lock.release()


def test_chrome_error_classification_does_not_hide_profile_conflicts() -> None:
    assert ChromeManager._looks_like_chrome_profile_conflict(
        RuntimeError("user data directory is already in use")
    )
    assert not ChromeManager._looks_like_missing_chrome_channel(
        RuntimeError("user data directory is already in use")
    )
    assert ChromeManager._looks_like_missing_chrome_channel(
        RuntimeError("Chromium distribution 'chrome' is not found")
    )


def test_structured_formatter_masks_sensitive_values() -> None:
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "authorization=Bearer secret-value password=hunter2",
        (),
        None,
    )
    payload = json.loads(StructuredJsonFormatter().format(record))

    assert "secret-value" not in payload["message"]
    assert "hunter2" not in payload["message"]
    assert payload["message"].count("[REDACTED]") == 2
