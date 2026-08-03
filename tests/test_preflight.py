from __future__ import annotations

import importlib.util
from dataclasses import replace

from app.config.facebook_browser import FacebookBrowserConfig
from app.config.settings import Settings
from app.preflight import CheckStatus, run_preflight


def test_missing_playwright_is_structured_failure_before_runtime_mutation(
    tmp_path, monkeypatch
):
    settings = replace(
        Settings.from_env(tmp_path / "missing.env"),
        facebook_target_url="https://www.facebook.com/test",
        database_path=tmp_path / "data" / "jobs.sqlite3",
        log_dir=tmp_path / "logs",
        job_data_dir=tmp_path / "jobs",
    )
    config = replace(
        FacebookBrowserConfig.load(),
        lock_path=tmp_path / "runtime" / "locks" / "facebook_browser.lock",
        profile_path=tmp_path / "runtime" / "profile",
        queue_database_path=tmp_path / "runtime" / "queue.db",
    )
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda name: None if name == "playwright" else real_find_spec(name),
    )

    report = run_preflight(settings, config, mode="quick", write_report=False)

    package_check = next(check for check in report.checks if check.name == "python_packages")
    composition_check = next(
        check for check in report.checks if check.name == "composition_root"
    )
    assert report.overall_status == "FAIL"
    assert package_check.status is CheckStatus.FAILED
    assert composition_check.status is CheckStatus.SKIPPED
    assert not config.lock_path.exists()
    assert not settings.database_path.exists()
