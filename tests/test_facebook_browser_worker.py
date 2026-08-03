from __future__ import annotations

import asyncio
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.browser.facebook_browser_manager import FacebookBrowserError, FacebookBrowserManager
from app.browser.facebook_browser_worker import FacebookBrowserWorker
from app.browser.facebook_job import FacebookJob, FacebookJobStatus, FacebookJobStore, FacebookJobType
from app.config.facebook_browser import FacebookBrowserConfig


def config_for(tmp_path: Path) -> FacebookBrowserConfig:
    return replace(
        FacebookBrowserConfig.load(),
        profile_path=tmp_path / "profile",
        executable_path=tmp_path / "chrome",
        lock_path=tmp_path / "locks" / "facebook.lock",
        pid_path=tmp_path / "pids" / "chrome.pid",
        diagnostics_path=tmp_path / "diagnostics",
        downloads_path=tmp_path / "downloads",
        queue_database_path=tmp_path / "jobs.sqlite3",
        startup_timeout_seconds=0.02,
        retry_delay_seconds=0.001,
        lock_timeout_seconds=1,
        max_start_attempts=1,
    )


@pytest.mark.asyncio
async def test_running_cdp_does_not_start_another_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = FacebookBrowserManager(config=config_for(tmp_path))
    monkeypatch.setattr(manager, "is_cdp_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(manager, "_validate_cdp_owner", lambda: 4242)
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not launch")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    await manager.ensure_chrome()
    assert not called


@pytest.mark.asyncio
async def test_missing_cdp_starts_chrome_exactly_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = config_for(tmp_path)
    config.executable_path.touch()
    manager = FacebookBrowserManager(config=config)
    monkeypatch.setattr(manager, "is_cdp_ready", AsyncMock(side_effect=[False, False, False, True]))
    processes = []

    class Process:
        pid = 321

        def poll(self):
            return None

    def launch(*args, **kwargs):
        processes.append((args, kwargs))
        return Process()

    monkeypatch.setattr(subprocess, "Popen", launch)
    await manager.ensure_chrome()
    assert len(processes) == 1
    assert config.pid_path.read_text() == "321"


@pytest.mark.asyncio
async def test_crashed_start_is_bounded_and_does_not_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = config_for(tmp_path)
    config.executable_path.touch()
    manager = FacebookBrowserManager(config=config)
    monkeypatch.setattr(manager, "is_cdp_ready", AsyncMock(return_value=False))
    launches = 0

    class Process:
        pid = 456

        def poll(self):
            return 1

    def launch(*args, **kwargs):
        nonlocal launches
        launches += 1
        return Process()

    monkeypatch.setattr(subprocess, "Popen", launch)
    with pytest.raises(FacebookBrowserError):
        await manager.ensure_chrome()
    assert launches == 1


class FakePage:
    url = "https://www.facebook.com/test"

    def is_closed(self):
        return False


class FakeTabs:
    def __init__(self):
        self.page = FakePage()
        self.released = []

    async def get(self, *args, **kwargs):
        return self.page

    async def release_job(self, job_id):
        self.released.append(job_id)


class FakeManager:
    def __init__(self):
        self.tabs = FakeTabs()
        self.browser_process_id = 99
        self.starts = 0
        self.close_calls = 0

    async def start(self):
        self.starts += 1

    async def close(self):
        self.close_calls += 1

    async def save_diagnostics(self, *args, **kwargs):
        raise AssertionError("unexpected diagnostic")


@pytest.mark.asyncio
async def test_worker_serializes_concurrent_jobs_and_keeps_browser_open(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    store = FacebookJobStore(config.queue_database_path)
    manager = FakeManager()
    worker = FacebookBrowserWorker(manager=manager, store=store, config=config)
    active = 0
    maximum = 0
    order = []

    async def handler(job, page):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        order.append(f"start:{job.job_id}")
        await asyncio.sleep(0.02)
        order.append(f"end:{job.job_id}")
        active -= 1
        return {"ok": True}

    worker.register_handler(FacebookJobType.CHECK_LOGIN, handler)
    first = store.create(FacebookJob(FacebookJobType.CHECK_LOGIN, {}))
    second = store.create(FacebookJob(FacebookJobType.CHECK_LOGIN, {}))
    results = await asyncio.gather(worker.execute(first), worker.execute(second))

    assert maximum == 1
    assert order == [f"start:{first.job_id}", f"end:{first.job_id}", f"start:{second.job_id}", f"end:{second.job_id}"]
    assert all(item.status is FacebookJobStatus.SUCCESS for item in results)
    assert manager.close_calls == 0


def test_interrupted_job_recovers_to_retry_waiting(tmp_path: Path) -> None:
    store = FacebookJobStore(tmp_path / "jobs.sqlite3")
    job = store.create(FacebookJob(FacebookJobType.CHECK_LOGIN, {}))
    job.status = FacebookJobStatus.RUNNING
    store.update(job)
    assert store.recover_interrupted() == 1
    recovered = store.get(job.job_id)
    assert recovered is not None
    assert recovered.status is FacebookJobStatus.RETRY_WAITING
    assert recovered.retry_count == 1


def test_only_central_manager_contains_browser_launch_calls() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = ("webdriver.Chrome(", "chromium.launch(", "launch_persistent_context(", "connect_over_cdp(")
    violations = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        dependency_tree = bool({".venv", "venv", "env", "site-packages"} & set(path.parts))
        if (
            relative.startswith("tests/")
            or dependency_tree
            or relative == "app/browser/facebook_browser_manager.py"
        ):
            continue
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                violations.append(f"{relative}: {marker}")
    assert violations == []


def test_profile_has_one_canonical_value() -> None:
    config = FacebookBrowserConfig.load()
    assert config.profile_path.as_posix().endswith("runtime/chrome_profiles/cdha_automation")
