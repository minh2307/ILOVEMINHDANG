from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.adapters.downloadreel_adapter import DownloadReelAdapter
from app.bootstrap import DependencyContainer
from app.browser.facebook_browser_cli import load_official_browser_configuration
from app.browser.facebook_browser_manager import FacebookBrowserError, FacebookBrowserManager, ProfileInUseError
from app.config.facebook_browser import FacebookBrowserConfig
from app.config.settings import CookieConfigurationError, Settings
from app.repositories.job_repository import JobRepository


_ALIAS_ENV = (
    "FACEBOOK_PROFILE_PATH",
    "FACEBOOK_CHROME_EXECUTABLE",
    "FACEBOOK_BROWSER_HEADLESS",
    "FACEBOOK_QUEUE_DATABASE_PATH",
    "FB_POSTER_PROFILE",
)


def clean_browser_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ALIAS_ENV:
        monkeypatch.delenv(name, raising=False)


def test_relative_browser_and_cookie_paths_are_root_relative_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    monkeypatch.setenv("CHROME_PROFILE_DIR", "runtime/chrome_profiles/cdha_automation")
    monkeypatch.setenv("FACEBOOK_COOKIE_FILE", "runtime/auth/facebook_cookies.txt")
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env(tmp_path / "missing.env")

    assert settings.chrome_profile_dir == (
        settings.project_root / "runtime/chrome_profiles/cdha_automation"
    ).resolve()
    assert settings.facebook_cookie_file == (
        settings.project_root / "runtime/auth/facebook_cookies.txt"
    ).resolve()


def test_compatibility_load_delegates_to_authoritative_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    monkeypatch.setenv("CHROME_PROFILE_DIR", str(tmp_path / "canonical-profile"))

    settings, cli_config = load_official_browser_configuration(
        env_file=tmp_path / "missing.env"
    )
    compatibility_config = FacebookBrowserConfig.load(
        env_file=tmp_path / "missing.env"
    )

    assert cli_config == FacebookBrowserConfig.from_settings(settings)
    assert compatibility_config == cli_config


def test_conflicting_profile_alias_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    monkeypatch.setenv("CHROME_PROFILE_DIR", str(tmp_path / "worker-profile"))
    monkeypatch.setenv("FACEBOOK_PROFILE_PATH", str(tmp_path / "browser-cli-profile"))

    with pytest.raises(ValueError, match="CHROME_PROFILE_DIR.*FACEBOOK_PROFILE_PATH"):
        Settings.from_env(tmp_path / "missing.env")


def test_sanitized_configuration_and_fingerprint_never_contain_cookie_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    cookie = tmp_path / "facebook_cookies.txt"
    secret = "c_user\tTOP-SECRET-COOKIE-VALUE"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n.facebook.com\tTRUE\t/\tTRUE\t2000000000\t" + secret + "\n",
        encoding="utf-8",
    )
    settings = replace(
        Settings.from_env(tmp_path / "missing.env"),
        facebook_cookie_file=cookie,
    )

    output = json.dumps(settings.sanitized_runtime_configuration(), sort_keys=True)
    fingerprint = settings.configuration_fingerprint()

    assert str(cookie) in output
    assert "TOP-SECRET-COOKIE-VALUE" not in output
    assert "TOP-SECRET-COOKIE-VALUE" not in fingerprint
    assert len(fingerprint) == 64


def test_cookie_inspection_has_clear_safe_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    missing = tmp_path / "missing.txt"
    settings = replace(
        Settings.from_env(tmp_path / "missing.env"),
        facebook_cookie_file=missing,
    )
    assert settings.inspect_facebook_cookie().status == "missing_optional"

    secret = "DO-NOT-LEAK-ME"
    missing.write_text(secret, encoding="utf-8")
    with pytest.raises(CookieConfigurationError) as caught:
        settings.inspect_facebook_cookie()
    assert "invalid Netscape format" in str(caught.value)
    assert secret not in str(caught.value)

    missing.write_text(
        "# Netscape HTTP Cookie File\n.facebook.com\tTRUE\t/\tTRUE\t2000000000\tc_user\t123\n",
        encoding="utf-8",
    )
    assert settings.inspect_facebook_cookie().status == "ready"


def test_official_container_uses_one_profile_lock_and_startup_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    settings = replace(
        Settings.from_env(tmp_path / "missing.env"),
        chrome_profile_dir=(tmp_path / "profile").resolve(),
        facebook_cookie_file=(tmp_path / "cookies.txt").resolve(),
        database_path=(tmp_path / "jobs.sqlite3").resolve(),
        browser_lock_path=(tmp_path / "locks/browser.lock").resolve(),
        browser_pid_path=(tmp_path / "pids/browser.pid").resolve(),
        browser_download_dir=(tmp_path / "downloads").resolve(),
        diagnostic_directory=(tmp_path / "diagnostics").resolve(),
        facebook_target_url="https://www.facebook.com/test",
    )

    container = DependencyContainer(settings)

    assert container.browser_config.profile_path == settings.chrome_profile_dir
    assert container.browser_manager.config.profile_path == settings.chrome_profile_dir
    assert container.browser_lock.browser_profile == str(settings.chrome_profile_dir)
    assert container.browser_manager.browser_lock is container.browser_lock
    assert container.worker.startup_diagnostics["configuration_fingerprint"] == (
        settings.configuration_fingerprint()
    )


def test_official_downloader_receives_canonical_cookie_by_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    cookie = tmp_path / "cookies.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n.facebook.com\tTRUE\t/\tTRUE\t2000000000\tc_user\t123\n",
        encoding="utf-8",
    )
    settings = replace(
        Settings.from_env(tmp_path / "missing.env"),
        facebook_cookie_file=cookie,
    )
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    repository.initialize()
    captured: dict[str, object] = {}

    def process(url: str, *, scrape_browser_metadata: bool, cookie_path: Path | None):
        captured.update(url=url, scrape=scrape_browser_metadata, cookie_path=cookie_path)
        return SimpleNamespace(status="error", error_msg="expected test stop")

    monkeypatch.setattr(
        "app.adapters.downloadreel_adapter.importlib.import_module",
        lambda _name: SimpleNamespace(process_and_download_reel=process),
    )
    adapter = DownloadReelAdapter(settings, repository)
    downloader = adapter._get_downloader()
    downloader("https://www.facebook.com/reel/123")

    assert captured["cookie_path"] == cookie.resolve()
    assert captured["scrape"] is False


def test_root_cookie_is_not_silently_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    (tmp_path / "Cookie.txt").write_text("secret=legacy", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    settings = replace(
        Settings.from_env(tmp_path / "missing.env"),
        facebook_cookie_file=tmp_path / "runtime/auth/facebook_cookies.txt",
    )

    inspection = settings.inspect_facebook_cookie()

    assert inspection.status == "missing_optional"
    assert inspection.path != (tmp_path / "Cookie.txt")


def test_browser_shell_scripts_are_thin_and_do_not_hardcode_profile_or_kill() -> None:
    root = Path(__file__).resolve().parents[1]
    expectations = {
        "start_facebook_browser.sh": "facebook_browser_cli start",
        "check_facebook_browser.sh": "facebook_browser_cli check",
        "stop_facebook_browser.sh": "facebook_browser_cli stop",
    }
    for name, invocation in expectations.items():
        text = (root / "scripts" / name).read_text(encoding="utf-8")
        assert invocation in text
        assert "chrome_profiles" not in text
        assert "pkill" not in text
        assert "killall" not in text


@pytest.mark.asyncio
async def test_unverified_chrome_profile_markers_are_never_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    settings = replace(
        Settings.from_env(tmp_path / "missing.env"),
        chrome_profile_dir=tmp_path / "profile",
        browser_lock_path=tmp_path / "locks/browser.lock",
    )
    config = FacebookBrowserConfig.from_settings(settings)
    config.profile_path.mkdir(parents=True)
    marker = config.profile_path / "SingletonLock"
    marker.write_text("unknown-owner", encoding="utf-8")
    manager = FacebookBrowserManager(settings=settings, config=config)
    monkeypatch.setattr(manager, "is_cdp_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(manager, "_listening_pids", lambda _port: [])

    with pytest.raises(ProfileInUseError, match="no profile data was modified"):
        await manager.ensure_chrome()

    assert marker.read_text(encoding="utf-8") == "unknown-owner"
    assert not list(config.profile_path.glob("SingletonLock.stale.*"))
    assert not config.lock_path.exists()


@pytest.mark.asyncio
async def test_browser_lock_is_released_when_startup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    settings = replace(
        Settings.from_env(tmp_path / "missing.env"),
        chrome_profile_dir=tmp_path / "profile",
        chrome_executable_fallback=tmp_path / "missing-chrome",
        browser_lock_path=tmp_path / "locks/browser.lock",
    )
    config = FacebookBrowserConfig.from_settings(settings)
    manager = FacebookBrowserManager(settings=settings, config=config)
    monkeypatch.setattr(manager, "is_cdp_ready", AsyncMock(return_value=False))

    with pytest.raises(FacebookBrowserError, match="Chrome executable not found"):
        await manager.ensure_chrome()

    assert not config.lock_path.exists()


def test_dotenv_values_do_not_leak_legacy_aliases_between_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    monkeypatch.delenv("CHROME_PROFILE_DIR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CHROME_PROFILE_DIR=runtime/chrome_profiles/cdha_automation\n"
        "FB_POSTER_PROFILE=runtime/chrome_profiles/cdha_automation\n",
        encoding="utf-8",
    )

    first = Settings.from_env(env_file)
    second = Settings.from_env(tmp_path / "missing.env")

    assert first.chrome_profile_dir == second.chrome_profile_dir
    assert "FB_POSTER_PROFILE" not in __import__("os").environ


def test_orphan_legacy_profile_alias_cannot_override_canonical_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_browser_env(monkeypatch)
    monkeypatch.delenv("CHROME_PROFILE_DIR", raising=False)
    monkeypatch.setenv("FB_POSTER_PROFILE", str(tmp_path / "legacy-profile"))

    settings = Settings.from_env(tmp_path / "missing.env")

    assert settings.chrome_profile_dir.as_posix().endswith(
        "runtime/chrome_profiles/cdha_automation"
    )
    assert settings.chrome_profile_dir != (tmp_path / "legacy-profile")
