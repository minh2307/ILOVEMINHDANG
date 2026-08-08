from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from app.config.facebook_browser import FacebookBrowserConfig
from app.config.settings import Settings
from app.infrastructure.browser.file_browser_lock import FileBrowserLock
from app.preflight import (
    CheckStatus,
    PreflightCheckResult,
    PreflightReport,
    _classify_cdha_authentication,
    _full_browser_checks,
    _full_ollama_checks,
    _write_report,
    run_preflight,
)


def _check(
    status: CheckStatus,
    *,
    required: bool = True,
    name: str = "example",
) -> PreflightCheckResult:
    return PreflightCheckResult(
        name=name,
        category="test",
        required=required,
        status=status,
        message=status.value,
        duration_ms=1,
    )


def _report(*checks: PreflightCheckResult) -> PreflightReport:
    return PreflightReport.create(
        mode="full",
        checks=checks,
        started_at="2026-08-03T00:00:00+00:00",
        completed_at="2026-08-03T00:00:01+00:00",
        configuration_fingerprint="safe-fingerprint",
    )


def test_required_non_pass_states_can_never_produce_pass():
    for state in (
        CheckStatus.FAILED,
        CheckStatus.SKIPPED,
        CheckStatus.TIMEOUT,
        CheckStatus.UNKNOWN,
    ):
        report = _report(_check(state))
        assert report.overall_status == "FAIL"


def test_optional_warning_produces_warn_when_required_checks_pass():
    report = _report(
        _check(CheckStatus.PASSED, name="required"),
        _check(CheckStatus.WARNING, required=False, name="optional"),
    )

    assert report.overall_status == "WARN"


def test_all_required_checks_pass():
    report = _report(
        _check(CheckStatus.PASSED, name="one"),
        _check(CheckStatus.PASSED, name="two"),
    )

    assert report.overall_status == "PASS"


def test_missing_required_check_fails_completeness_validation():
    report = PreflightReport.create(
        mode="full",
        checks=(_check(CheckStatus.PASSED, name="runtime"),),
        required_check_names=("runtime", "ollama_inference"),
        started_at="2026-08-03T00:00:00+00:00",
        completed_at="2026-08-03T00:00:01+00:00",
        configuration_fingerprint="safe-fingerprint",
    )

    assert report.overall_status == "FAIL"
    assert report.missing_required_checks == ("ollama_inference",)


def test_ollama_not_executed_cannot_pass_full_mode():
    report = _report(
        _check(CheckStatus.PASSED, name="runtime"),
        _check(CheckStatus.SKIPPED, name="ollama_inference"),
    )

    assert report.overall_status == "FAIL"


def _settings(tmp_path):
    return replace(
        Settings.from_env(tmp_path / "missing.env"),
        facebook_target_url="https://www.facebook.com/test-target",
        ollama_model="minicpm-v",
        chrome_profile_dir=tmp_path / "runtime" / "profile",
        browser_lock_path=tmp_path / "runtime" / "locks" / "browser.lock",
        browser_pid_path=tmp_path / "runtime" / "pids" / "browser.pid",
        browser_download_dir=tmp_path / "runtime" / "downloads",
        database_path=tmp_path / "runtime" / "database" / "jobs.sqlite3",
        job_data_dir=tmp_path / "runtime" / "jobs",
        log_dir=tmp_path / "runtime" / "logs",
        screenshot_dir=tmp_path / "runtime" / "screenshots",
        diagnostic_directory=tmp_path / "runtime" / "diagnostics",
        facebook_cookie_file=tmp_path / "runtime" / "auth" / "cookies.txt",
        preflight_report_dir=tmp_path / "runtime" / "diagnostics" / "preflight",
    )


def test_quick_mode_never_invokes_external_probe(tmp_path, monkeypatch):
    settings = _settings(tmp_path)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("quick mode must not invoke external probes")

    monkeypatch.setattr("app.preflight._full_ollama_checks", forbidden)
    monkeypatch.setattr("app.preflight._full_browser_checks", forbidden)

    report = run_preflight(settings, mode="quick", write_report=False)

    external = {
        check.name: check for check in report.checks
        if check.name in {"ollama_inference", "browser_start", "facebook_authentication", "cdha_authentication"}
    }
    assert external
    assert all(check.status is CheckStatus.SKIPPED for check in external.values())
    assert all(not check.required for check in external.values())


@pytest.mark.asyncio
async def test_full_ollama_success_uses_official_adapter(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    calls = []

    class Analyzer:
        async def list_models(self):
            calls.append("models")
            return ("minicpm-v",)

        async def minimal_inference(self, prompt):
            calls.append(prompt)
            return "PREFLIGHT_OK"

    monkeypatch.setattr(
        "app.ai.provider_factory.build_analyzer",
        lambda *_args, **_kwargs: Analyzer(),
    )

    checks = await _full_ollama_checks(settings)

    assert [check.status for check in checks] == [
        CheckStatus.PASSED,
        CheckStatus.PASSED,
        CheckStatus.PASSED,
    ]
    assert calls == ["models", "Reply with exactly: PREFLIGHT_OK"]


@pytest.mark.asyncio
async def test_full_ollama_timeout_is_required_failure(tmp_path, monkeypatch):
    settings = replace(_settings(tmp_path), preflight_ollama_timeout_seconds=0.001)

    class Analyzer:
        async def list_models(self):
            await asyncio.sleep(0.05)

    monkeypatch.setattr(
        "app.ai.provider_factory.build_analyzer",
        lambda *_args, **_kwargs: Analyzer(),
    )

    checks = await _full_ollama_checks(settings)

    assert checks[0].status is CheckStatus.TIMEOUT
    assert checks[0].required is True
    assert checks[1].status is CheckStatus.SKIPPED
    assert checks[2].status is CheckStatus.SKIPPED


@pytest.mark.asyncio
async def test_full_ollama_bounds_transport_timeout_to_preflight(tmp_path, monkeypatch):
    settings = replace(
        _settings(tmp_path),
        ollama_timeout_seconds=300,
        preflight_ollama_timeout_seconds=0.2,
    )
    configured_timeouts = []

    class Analyzer:
        async def list_models(self):
            return ("minicpm-v",)

        async def minimal_inference(self, _prompt):
            return "PREFLIGHT_OK"

    def build_analyzer(configured, **_kwargs):
        configured_timeouts.append(configured.ollama_timeout_seconds)
        return Analyzer()

    monkeypatch.setattr("app.ai.provider_factory.build_analyzer", build_analyzer)

    checks = await _full_ollama_checks(settings)

    assert all(check.status is CheckStatus.PASSED for check in checks)
    assert configured_timeouts == [1]


@pytest.mark.asyncio
async def test_live_browser_lock_is_reported_and_never_deleted(tmp_path):
    settings = _settings(tmp_path)
    config = FacebookBrowserConfig.from_settings(settings)
    owner = FileBrowserLock(
        str(config.lock_path),
        process_name="test-owner",
        browser_profile=str(config.profile_path),
        browser_port=config.cdp_port,
        timeout_seconds=config.lock_timeout_seconds,
        heartbeat_seconds=config.lock_heartbeat_seconds,
    )
    assert await owner.acquire("preflight-lock-test")
    original = config.lock_path.read_bytes()
    try:
        checks = await _full_browser_checks(settings, config)
        by_name = {check.name: check for check in checks}
        assert by_name["browser_lock"].status is CheckStatus.FAILED
        assert "BROWSER_BUSY" in by_name["browser_lock"].message
        assert by_name["browser_start"].status is CheckStatus.SKIPPED
        assert config.lock_path.read_bytes() == original
    finally:
        await owner.release()


@pytest.mark.asyncio
async def test_cdha_localized_login_title_is_classified_explicitly():
    class Page:
        url = "https://cdha.ai/dash"

        async def title(self):
            return "@CDHa.ai • Đăng nhập"

    class Resolver:
        async def exists(self, _page, _key, **_kwargs):
            return False

    class Client:
        async def is_authenticated(self, _page):
            pytest.fail("a visible login title must be classified before auth fallback")

    authenticated, state = await _classify_cdha_authentication(
        Page(), Client(), Resolver()
    )

    assert authenticated is False
    assert state == "LOGIN_REQUIRED"


def test_machine_report_redacts_sensitive_details(tmp_path):
    report = _report(PreflightCheckResult(
        name="safe_report",
        category="diagnostics",
        required=True,
        status=CheckStatus.PASSED,
        message="ok",
        duration_ms=5,
        details={
            "cookie_value": "super-secret-cookie",
            "token": "super-secret-token",
            "cookie_path": tmp_path / "cookies.txt",
            "safe": "visible",
        },
    ))
    path = tmp_path / "preflight.json"

    _write_report(report, path)
    payload = path.read_text(encoding="utf-8")

    assert "super-secret" not in payload
    assert "visible" in payload
    assert json.loads(payload)["checks"][0]["duration_ms"] == 5
