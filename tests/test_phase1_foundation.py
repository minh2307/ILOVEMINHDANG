from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from app.browser.chrome_manager import ChromeManager, ProfileInUseError
from app.browser.selector_resolver import SelectorResolver
from app.config.settings import Settings
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


def test_selector_configuration_loads_all_services() -> None:
    settings = Settings.from_env(env_file=Path("/nonexistent-cdha-env"))
    resolver = SelectorResolver(settings.selectors_path)

    assert resolver.candidates("gemini.prompt_input")
    assert resolver.candidates("cdha.video_input")
    assert resolver.candidates("facebook.post_button")
    with pytest.raises(KeyError):
        resolver.candidates("missing.selector")


def test_profile_lock_rejects_second_manager(tmp_path: Path) -> None:
    settings = replace(
        Settings.from_env(env_file=Path("/nonexistent-cdha-env")),
        chrome_profile_dir=tmp_path / "profile",
    )
    first = ChromeManager(settings)
    second = ChromeManager(settings)
    first._acquire_profile_lock()
    try:
        with pytest.raises(ProfileInUseError):
            second._acquire_profile_lock()
    finally:
        first._release_profile_lock()


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
