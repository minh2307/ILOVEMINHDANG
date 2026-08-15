from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.application.use_cases.resume_job_use_case import ResumeJobUseCase
from app.application.use_cases.resolve_publication_decision_use_case import (
    ResolvePublicationDecisionUseCase,
)
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.application.use_cases.retry_job_use_case import RetryJobUseCase
from app.browser.cdha_client import CDHAWebClient
from app.browser.facebook_client import FacebookWebClient
from app.config.settings import Settings
from app.domain.enums.job_status import JobStatus
from app.domain.models.job import Job
from app.domain.enums.job_type import JobType
from app.domain.models.facebook_job import FacebookJob
from app.domain.policies.external_side_effect_policy import (
    CDHACheckpoint,
    LargeUploadApproval,
    facebook_submission_is_committed,
)
from app.errors import CDHAUploadError
from app.infrastructure.persistence.sqlite_job_repository import JobRepository
from app.infrastructure.persistence.sqlite_job_queue import SQLiteJobQueue
from app.services.screenshot_service import ScreenshotService
from scripts.recover_verified_permalinks import run_recovery


MIB = 1024 * 1024


def make_settings(tmp_path: Path, **changes: Any) -> Settings:
    values = {
        "database_path": tmp_path / "jobs.sqlite3",
        "job_data_dir": tmp_path / "jobs",
        "chrome_profile_dir": tmp_path / "profile",
        "cdha_large_file_threshold_mb": 50,
        "cdha_poll_interval_seconds": 0,
        "cdha_upload_timeout_seconds": 0.02,
        **changes,
    }
    return replace(Settings.from_env(env_file=tmp_path / "missing.env"), **values)


class MemoryRepository:
    def __init__(self, job: Job) -> None:
        self.job = job
        self.transitions: list[JobStatus] = []

    def get_job(self, job_id: str) -> Job | None:
        return self.job if self.job.job_id == job_id else None

    def update_data(self, job_id: str, patch: dict[str, Any]) -> Job:
        assert job_id == self.job.job_id
        self.job.data.update(patch)
        return self.job

    def transition(
        self,
        job_id: str,
        target: JobStatus,
        *,
        data_patch: dict[str, Any] | None = None,
        **_: Any,
    ) -> Job:
        assert job_id == self.job.job_id
        self.job.status = target
        self.job.data.update(data_patch or {})
        self.transitions.append(target)
        return self.job


class NoopChrome:
    context: Any = None


class ActionLocator:
    def __init__(self) -> None:
        self.trials = 0
        self.clicks = 0
        self.files: list[str] = []

    async def is_visible(self) -> bool:
        return True

    async def is_enabled(self) -> bool:
        return True

    async def click(self, **kwargs: Any) -> None:
        if kwargs.get("trial"):
            self.trials += 1
        else:
            self.clicks += 1

    async def set_input_files(self, value: str) -> None:
        self.files.append(value)


class ActionResolver:
    def __init__(self, analyze: ActionLocator) -> None:
        self.analyze = analyze

    async def find_first(self, _page: Any, key: str, **_: Any) -> Any:
        assert key == "cdha.analyze_button"
        return self.analyze

    async def exists(self, _page: Any, _key: str, **_: Any) -> bool:
        return False


def make_job(tmp_path: Path, *, size: int = 1, status: JobStatus = JobStatus.RETRY_PENDING) -> Job:
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"x" * size)
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    return Job(
        "job-full-id",
        "https://www.facebook.com/reel/1",
        status,
        data={
            "video_path": str(video.resolve()),
            "video_size_bytes": size,
            "checksum_sha256": digest,
            "cdha_checkpoint": CDHACheckpoint.UPLOAD_NOT_STARTED.value,
        },
    )


def test_analyze_is_not_clicked_until_upload_checkpoint_is_confirmed(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    job.data["cdha_checkpoint"] = CDHACheckpoint.UPLOAD_IN_PROGRESS.value
    repo = MemoryRepository(job)
    locator = ActionLocator()
    client = CDHAWebClient(
        make_settings(tmp_path), repo, NoopChrome(), resolver=ActionResolver(locator)
    )

    with pytest.raises(CDHAUploadError, match="UPLOAD_CONFIRMED"):
        asyncio.run(client._request_analysis_once(object(), job_id=job.job_id))

    assert locator.trials == 0
    assert locator.clicks == 0


def test_actionable_analyze_is_clicked_exactly_once(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    job.data["cdha_checkpoint"] = CDHACheckpoint.UPLOAD_CONFIRMED.value
    repo = MemoryRepository(job)
    locator = ActionLocator()
    client = CDHAWebClient(
        make_settings(tmp_path), repo, NoopChrome(), resolver=ActionResolver(locator)
    )

    asyncio.run(client._request_analysis_once(object(), job_id=job.job_id))

    assert locator.trials == 1
    assert locator.clicks == 1


class StabilityClient(CDHAWebClient):
    def __init__(self, *args: Any, signals: list[dict[str, bool]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.signals = list(signals)

    async def _upload_stability_signals(self, _page: Any, _video: Path) -> dict[str, bool]:
        return self.signals.pop(0) if len(self.signals) > 1 else self.signals[0]


def test_overlay_or_progress_prevents_upload_confirmation_and_analyze(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    job.data["cdha_checkpoint"] = CDHACheckpoint.UPLOAD_IN_PROGRESS.value
    repo = MemoryRepository(job)
    locator = ActionLocator()
    blocked = {
        "completion": True,
        "file_identity": True,
        "progress_absent": False,
        "analyze_actionable": False,
    }
    client = StabilityClient(
        make_settings(tmp_path),
        repo,
        NoopChrome(),
        resolver=ActionResolver(locator),
        signals=[blocked],
    )
    with pytest.raises(CDHAUploadError, match="stable multi-signal"):
        asyncio.run(client._wait_for_stable_upload_completion(
            object(), Path(job.data["video_path"]), job_id=job.job_id
        ))
    with pytest.raises(CDHAUploadError):
        asyncio.run(client._request_analysis_once(object(), job_id=job.job_id))
    assert locator.clicks == 0


def test_two_stable_observations_confirm_then_analyze_once(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    job.data["cdha_checkpoint"] = CDHACheckpoint.UPLOAD_IN_PROGRESS.value
    repo = MemoryRepository(job)
    locator = ActionLocator()
    ready = {
        "completion": True,
        "file_identity": True,
        "progress_absent": True,
        "analyze_actionable": True,
    }
    client = StabilityClient(
        make_settings(tmp_path),
        repo,
        NoopChrome(),
        resolver=ActionResolver(locator),
        signals=[ready, ready],
    )
    asyncio.run(client._wait_for_stable_upload_completion(
        object(), Path(job.data["video_path"]), job_id=job.job_id
    ))
    asyncio.run(client._request_analysis_once(object(), job_id=job.job_id))
    assert locator.clicks == 1
    assert job.data["cdha_checkpoint"] == CDHACheckpoint.ANALYSIS_REQUESTED.value
    with pytest.raises(CDHAUploadError, match="reconciliation"):
        asyncio.run(client._request_analysis_once(object(), job_id=job.job_id))
    assert locator.clicks == 1


def test_legacy_submit_checkpoint_is_reconciliation_only() -> None:
    assert CDHACheckpoint.from_data({"cdha_submission_state": "SUBMITTING"}) is CDHACheckpoint.UPLOAD_IN_PROGRESS
    assert CDHACheckpoint.from_data({"cdha_submission_state": "SUBMITTED"}) is CDHACheckpoint.ANALYSIS_CONFIRMED


@pytest.mark.parametrize(
    ("checkpoint", "reconciliation_only"),
    [
        (CDHACheckpoint.UPLOAD_NOT_STARTED, False),
        (CDHACheckpoint.UPLOAD_IN_PROGRESS, True),
        (CDHACheckpoint.UPLOAD_CONFIRMED, False),
        (CDHACheckpoint.ANALYSIS_REQUESTED, True),
        (CDHACheckpoint.ANALYSIS_CONFIRMED, True),
    ],
)
def test_crash_restart_checkpoint_policy_is_monotonic(
    checkpoint: CDHACheckpoint, reconciliation_only: bool
) -> None:
    assert checkpoint.reconciliation_only is reconciliation_only


class BranchClient(CDHAWebClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cdp_paths: list[Path] = []

    async def _reconcile_existing_upload(self, _page: Any, _video: Path) -> str:
        return "not_started"

    async def _set_large_file_input_via_cdp(
        self, _page: Any, _input: Any, file_path: Path
    ) -> None:
        self.cdp_paths.append(file_path)


def test_small_file_uses_existing_playwright_path(tmp_path: Path) -> None:
    job = make_job(tmp_path, size=8)
    repo = MemoryRepository(job)
    locator = ActionLocator()
    client = BranchClient(make_settings(tmp_path), repo, NoopChrome(), resolver=object())

    asyncio.run(client._upload_video_file(object(), locator, Path(job.data["video_path"]), job_id=job.job_id))

    assert locator.files == [job.data["video_path"]]
    assert client.cdp_paths == []


def test_large_file_uses_direct_path_without_buffering_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = make_job(tmp_path, size=8)
    job.data["video_size_bytes"] = 51 * MIB
    job.data["cdha_large_upload_approval"] = LargeUploadApproval.grant_data(
        job.job_id, job.data["checksum_sha256"], 51 * MIB
    )
    repo = MemoryRepository(job)
    locator = ActionLocator()
    settings = make_settings(tmp_path, cdha_large_file_threshold_mb=1 / MIB)
    client = BranchClient(settings, repo, NoopChrome(), resolver=object())
    monkeypatch.setattr(client, "_validate_video_metadata", lambda *_: (51 * MIB, job.data["checksum_sha256"]))

    asyncio.run(client._upload_video_file(object(), locator, Path(job.data["video_path"]), job_id=job.job_id))

    assert locator.files == []
    assert client.cdp_paths == [Path(job.data["video_path"])]
    assert job.data["cdha_large_upload_approval"]["state"] == "CONSUMED"


def test_large_file_on_non_local_browser_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = make_job(tmp_path, size=8)
    job.data["video_size_bytes"] = 51 * MIB
    job.data["cdha_large_upload_approval"] = LargeUploadApproval.grant_data(
        job.job_id, job.data["checksum_sha256"], 51 * MIB
    )
    repo = MemoryRepository(job)
    settings = make_settings(
        tmp_path, cdha_large_file_threshold_mb=1 / MIB, browser_cdp_host="remote.example"
    )
    client = CDHAWebClient(settings, repo, NoopChrome(), resolver=object())
    async def not_started(_page: Any, _video: Path) -> str:
        return "not_started"
    monkeypatch.setattr(client, "_reconcile_existing_upload", not_started)
    monkeypatch.setattr(client, "_validate_video_metadata", lambda *_: (51 * MIB, job.data["checksum_sha256"]))

    with pytest.raises(CDHAUploadError, match="same host"):
        asyncio.run(client._upload_video_file(object(), ActionLocator(), Path(job.data["video_path"]), job_id=job.job_id))


class MarkerLocator:
    def __init__(self) -> None:
        self.marker = ""

    async def evaluate(self, expression: str, value: str | None = None) -> None:
        if "setAttribute" in expression:
            self.marker = str(value)


class CDPSession:
    def __init__(self, locator: MarkerLocator) -> None:
        self.locator = locator
        self.commands: list[tuple[str, dict[str, Any] | None]] = []
        self.detached = False

    async def send(self, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.commands.append((command, params))
        if command == "DOM.getDocument":
            return {
                "root": {
                    "backendNodeId": 1,
                    "children": [{
                        "backendNodeId": 77,
                        "attributes": ["data-cdha-upload-token", self.locator.marker],
                    }],
                }
            }
        return {}

    async def detach(self) -> None:
        self.detached = True


class CDPContext:
    def __init__(self, session: CDPSession) -> None:
        self.session = session

    async def new_cdp_session(self, _page: Any) -> CDPSession:
        return self.session


def test_direct_cdp_sets_only_absolute_path_and_backend_node(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    locator = MarkerLocator()
    session = CDPSession(locator)
    page = SimpleNamespace(context=CDPContext(session))
    client = CDHAWebClient(make_settings(tmp_path), MemoryRepository(job), NoopChrome(), resolver=object())
    video = Path(job.data["video_path"])

    asyncio.run(client._set_large_file_input_via_cdp(page, locator, video))

    set_file = next(item for item in session.commands if item[0] == "DOM.setFileInputFiles")
    assert set_file[1] == {"files": [str(video.resolve())], "backendNodeId": 77}
    assert session.detached is True



class QueueFake:
    def __init__(self) -> None:
        self.items: list[Any] = []

    async def enqueue(self, item: Any) -> bool:
        self.items.append(item)
        return True


def test_unapproved_large_retry_is_not_scheduled_or_consumed(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    job.data["video_size_bytes"] = 51 * MIB
    repo = MemoryRepository(job)
    queue = QueueFake()
    scheduler = ScheduleWorkflowJobsUseCase(repo, queue, cdha_large_file_threshold_bytes=50 * MIB)

    queued = asyncio.run(scheduler.schedule_job(job.job_id))

    assert queued is False
    assert queue.items == []
    assert job.attempt_count == 0


def test_existing_created_queue_item_is_not_claimed_without_approval(tmp_path: Path) -> None:
    queue = SQLiteJobQueue(str(tmp_path / "queue.sqlite3"), claim_eligibility=lambda _payload: False)
    item = FacebookJob(
        job_id="existing-created",
        job_type=JobType.PROCESS_WORKFLOW,
        payload={"workflow_job_id": "large-job"},
    )
    assert asyncio.run(queue.enqueue(item)) is True
    assert asyncio.run(queue.dequeue(worker_id="worker")) is None
    record = asyncio.run(queue.get_record(item.job_id))
    assert record["status"] == JobStatus.CREATED.value
    assert record["attempt_count"] == 0


@pytest.mark.parametrize("wrong", ["job", "hash", "size"])
def test_large_upload_approval_rejects_wrong_scope(tmp_path: Path, wrong: str) -> None:
    job = make_job(tmp_path)
    repo = MemoryRepository(job)
    queue = QueueFake()
    scheduler = ScheduleWorkflowJobsUseCase(repo, queue, cdha_large_file_threshold_bytes=1)
    resume = ResumeJobUseCase(repo, scheduler, cdha_large_file_threshold_bytes=1)
    jid = "wrong" if wrong == "job" else job.job_id
    sha = "0" * 64 if wrong == "hash" else job.data["checksum_sha256"]
    size = 99 if wrong == "size" else job.data["video_size_bytes"]

    result = asyncio.run(
        resume.execute(
            job.job_id,
            large_upload_job_id=jid,
            large_upload_sha256=sha,
            large_upload_size_bytes=size,
            confirmation=LargeUploadApproval.expected_phrase(jid, sha, size),
        )
    )

    assert result.success is False
    assert queue.items == []
    assert "cdha_large_upload_approval" not in job.data


def test_large_upload_approval_dry_run_does_not_persist_or_enqueue(tmp_path: Path) -> None:
    job = make_job(tmp_path, size=8)
    repo = MemoryRepository(job)
    queue = QueueFake()
    scheduler = ScheduleWorkflowJobsUseCase(repo, queue, cdha_large_file_threshold_bytes=1)
    resume = ResumeJobUseCase(repo, scheduler, cdha_large_file_threshold_bytes=1)
    phrase = LargeUploadApproval.expected_phrase(
        job.job_id, job.data["checksum_sha256"], job.data["video_size_bytes"]
    )
    result = asyncio.run(resume.execute(
        job.job_id,
        large_upload_job_id=job.job_id,
        large_upload_sha256=job.data["checksum_sha256"],
        large_upload_size_bytes=job.data["video_size_bytes"],
        confirmation=phrase,
        dry_run=True,
    ))
    assert result.success is True
    assert result.data["dry_run"] is True
    assert "cdha_large_upload_approval" not in job.data
    assert queue.items == []


class NoBrowserChrome:
    def __init__(self) -> None:
        self.new_page_calls = 0

    async def new_page(self) -> Any:
        self.new_page_calls += 1
        raise AssertionError("timeline scan must not start")


def make_verified_facebook_job(*, post_id: str | None = "123") -> Job:
    data: dict[str, Any] = {
        "facebook_publication_verified": True,
        "facebook_submission_status": "RECONCILED_VERIFIED",
        "facebook_post_url": "https://facebook.com/page/posts/123?tracking=1",
        "facebook_publication_started_at": "2026-01-01T00:00:00+00:00",
    }
    if post_id is not None:
        data["facebook_post_id"] = post_id
    return Job("fb-job", "https://facebook.com/reel/1", JobStatus.POST_URL_EXTRACTION_FAILED, data=data)


def test_verified_permalink_short_circuits_timeline_scan(tmp_path: Path) -> None:
    job = make_verified_facebook_job()
    repo = MemoryRepository(job)
    chrome = NoBrowserChrome()
    client = FacebookWebClient(make_settings(tmp_path), repo, chrome, resolver=object())

    result = asyncio.run(
        client.extract_new_post_permalink(
            job_id=job.job_id,
            publication_started_at=SimpleNamespace(),
        )
    )

    assert result.success is True
    assert result.post_url == "https://www.facebook.com/page/posts/123"
    assert chrome.new_page_calls == 0
    assert job.status is JobStatus.POST_URL_EXTRACTED


def test_verified_submission_can_retry_only_the_permalink_step(tmp_path: Path) -> None:
    job = make_verified_facebook_job()
    repo = MemoryRepository(job)
    queue = QueueFake()
    scheduler = ScheduleWorkflowJobsUseCase(repo, queue)
    result = asyncio.run(RetryJobUseCase(repo, scheduler).execute(job.job_id))
    assert result.success is True
    assert job.status is JobStatus.RETRY_PENDING
    assert job.data["retry_step"] == "facebook_permalink"
    assert len(queue.items) == 1


def test_verified_permalink_without_post_id_does_not_fail(tmp_path: Path) -> None:
    job = make_verified_facebook_job(post_id=None)
    job.data["facebook_post_url"] = "https://facebook.com/share/p/opaque-value"
    repo = MemoryRepository(job)
    client = FacebookWebClient(make_settings(tmp_path), repo, NoBrowserChrome(), resolver=object())

    result = asyncio.run(
        client.extract_new_post_permalink(job_id=job.job_id, publication_started_at=SimpleNamespace())
    )

    assert result.success is True
    assert result.post_id is None


def test_post_submit_checkpoint_is_irreversible() -> None:
    data = {
        "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
        "facebook_reconciliation_exhausted": True,
    }
    assert facebook_submission_is_committed(data) is True
    from app.domain.rules.state_transitions import JobStateTransitions

    targets = JobStateTransitions.allowed_targets(
        JobStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED
    )
    assert JobStatus.FACEBOOK_PREPARING not in targets
    assert JobStatus.FACEBOOK_PUBLISHING not in targets


def test_client_boundary_blocks_second_publish_even_with_stale_manual_status(tmp_path: Path) -> None:
    job = Job(
        "stale-manual",
        "https://facebook.com/reel/1",
        JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        data={"facebook_submission_status": "SUBMITTED_UNCONFIRMED"},
    )
    client = FacebookWebClient(
        make_settings(tmp_path), MemoryRepository(job), NoBrowserChrome(), resolver=object()
    )
    with pytest.raises(ValueError, match="second Publish"):
        asyncio.run(client.publish_prepared_post(job_id=job.job_id))


def test_operator_can_attach_permalink_with_audited_read_only_decision(tmp_path: Path) -> None:
    job = Job(
        "exhausted",
        "https://facebook.com/reel/1",
        JobStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED,
        data={"facebook_submission_status": "SUBMITTED_UNCONFIRMED"},
    )
    repo = MemoryRepository(job)
    use_case = ResolvePublicationDecisionUseCase(repo)
    decision = "attach-permalink"
    result = asyncio.run(use_case.execute(
        job.job_id,
        decision=decision,
        permalink="https://facebook.com/page/posts/456?ref=x",
        confirmation=use_case.expected_phrase(job.job_id, decision),
    ))
    assert result.success is True
    assert job.status is JobStatus.FACEBOOK_PUBLISHED
    assert job.data["facebook_post_url"] == "https://www.facebook.com/page/posts/456"
    assert result.data["external_action_performed"] is False


class AtomicPage:
    async def set_viewport_size(self, _size: dict[str, int]) -> None: return None
    async def add_style_tag(self, **_: Any) -> None: return None
    async def evaluate(self, *_: Any) -> None: return None
    async def wait_for_function(self, *_: Any, **__: Any) -> None: return None
    async def wait_for_timeout(self, *_: Any) -> None: return None

    async def screenshot(self, *, path: str, **_: Any) -> None:
        Path(path).write_bytes(b"complete-png")


class MissingAnchors:
    async def find_first(self, *_: Any, **__: Any) -> Any:
        raise KeyError("fixture has no anchors")


def test_screenshot_fallback_is_written_atomically(tmp_path: Path) -> None:
    service = ScreenshotService(MissingAnchors())
    paths, warnings = asyncio.run(service.capture_required(AtomicPage(), tmp_path / "job"))
    assert len(paths) == 2
    assert warnings
    assert all(path.read_bytes() == b"complete-png" for path in paths)
    assert not list((tmp_path / "job" / "screenshots").glob("*.tmp-*"))


class RetryCapture:
    def __init__(self) -> None:
        self.calls = 0

    async def capture_required(self, _page: Any, job_dir: Path):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Execution context was destroyed")
        return [job_dir / "screenshots" / "ok.png"], []


class NavigationPage:
    def __init__(self) -> None:
        self.url = "https://cdha.ai/consultation"
        self.goto_calls = 0

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None: return None

    async def goto(self, url: str, **_: Any) -> None:
        self.goto_calls += 1
        self.url = url


def test_capture_retry_reacquires_result_without_repeating_share(tmp_path: Path) -> None:
    job = make_job(tmp_path)
    screenshots = RetryCapture()
    page = NavigationPage()
    client = CDHAWebClient(
        make_settings(tmp_path),
        MemoryRepository(job),
        NoopChrome(),
        resolver=object(),
        screenshots=screenshots,
    )
    paths, _warnings = asyncio.run(client._capture_result_screenshots(
        page, tmp_path / "job", result_url="https://cdha.ai/dash?view=44100"
    ))
    assert screenshots.calls == 2
    assert page.goto_calls == 1
    assert paths[0].name == "ok.png"


def _historical_permalink_database(tmp_path: Path) -> tuple[Path, str]:
    db = tmp_path / "history.sqlite3"
    repo = JobRepository(db)
    repo.initialize()
    job = repo.create_job("https://facebook.com/reel/9", job_id="history")
    payload = {
        "facebook_publication_verified": True,
        "facebook_submission_status": "RECONCILED_VERIFIED",
        "facebook_post_url": "https://facebook.com/page/posts/999?ref=tracking",
    }
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE jobs SET status=?, data_json=?, output_payload_json=? WHERE job_id=?",
            (JobStatus.POST_URL_EXTRACTION_FAILED.value, json.dumps(payload), json.dumps(payload), job.job_id),
        )
    return db, job.job_id


def test_permalink_recovery_dry_run_does_not_write(tmp_path: Path) -> None:
    db, job_id = _historical_permalink_database(tmp_path)
    before = db.read_bytes()
    report = run_recovery(db, apply=False)
    assert report["mode"] == "dry-run"
    assert [row["job_id"] for row in report["eligible"]] == [job_id]
    assert db.read_bytes() == before
    assert not list(tmp_path.glob("*_backup_*.sqlite3"))


def test_permalink_recovery_apply_backs_up_and_only_updates_eligible(tmp_path: Path) -> None:
    db, job_id = _historical_permalink_database(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO jobs(job_id, source_url, normalized_source_url, status, data_json, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            ("ineligible", "x", "x", JobStatus.POST_URL_EXTRACTION_FAILED.value, "{}", "now", "now"),
        )
    report = run_recovery(db, apply=True)
    assert Path(report["backup_path"]).is_file()
    with sqlite3.connect(db) as connection:
        rows = dict(connection.execute("SELECT job_id,status FROM jobs"))
    assert rows[job_id] == JobStatus.POST_URL_EXTRACTED.value
    assert rows["ineligible"] == JobStatus.POST_URL_EXTRACTION_FAILED.value
