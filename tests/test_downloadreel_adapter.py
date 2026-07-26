from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from app.adapters.downloadreel_adapter import DownloadReelAdapter, DownloadReelCoordinator
from app.main import build_parser
from app.config.settings import Settings
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.reel_normalization import normalize_reel_url


REEL_URL = "https://www.facebook.com/reel/123/?mibextid=test"
NORMALIZED_URL = "https://www.facebook.com/reel/123"


def test_cli_accepts_download_reel_and_force_download() -> None:
    args = build_parser().parse_args(["--download-reel", REEL_URL, "--force-download"])

    assert args.download_reel == REEL_URL
    assert args.force_download is True


def make_settings(tmp_path: Path) -> Settings:
    return replace(
        Settings.from_env(env_file=tmp_path / "missing.env"),
        database_path=tmp_path / "jobs.sqlite3",
        job_data_dir=tmp_path / "jobs",
        log_dir=tmp_path / "logs",
        screenshot_dir=tmp_path / "screenshots",
        chrome_profile_dir=tmp_path / "profile",
        downloadreel_dir=tmp_path / "legacy",
    )


def make_repository(settings: Settings) -> JobRepository:
    repository = JobRepository(settings.database_path)
    repository.initialize()
    return repository


def make_video_and_metadata(
    tmp_path: Path,
    *,
    name: str = "video.mp4",
    content: bytes = b"video-bytes",
) -> Path:
    video = tmp_path / name
    video.write_bytes(content)
    video.with_suffix(".json").write_text(
        json.dumps(
            {
                "video": video.name,
                "caption": "  Clinical caption  \nXem thêm",
                "comments": ["Useful finding", "Like", "Useful finding"],
                "commentDetails": [
                    {"author": "Public Doctor", "content": "Useful finding"},
                    {"author": None, "content": "Second finding\nSee more"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return video


def legacy_result(video: Path, *, status: str = "done", error: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        error_msg=error,
        file_path=str(video),
        downloaded_at="2026-07-22T10:00:00+00:00",
    )


def create_job(repository: JobRepository, url: str = REEL_URL) -> str:
    job = repository.create_job(
        url,
        normalized_source_url=normalize_reel_url(url),
    )
    return job.job_id


def test_successful_adapter_normalization_and_sqlite_transitions(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = make_repository(settings)
    video = make_video_and_metadata(tmp_path)
    adapter = DownloadReelAdapter(settings, repository, downloader=lambda _: legacy_result(video))
    job_id = create_job(repository)

    result = asyncio.run(adapter.process(REEL_URL, job_id))

    assert result.success
    assert result.video_path == video.resolve()
    assert result.video_filename == "video.mp4"
    assert result.video_size_bytes == len(b"video-bytes")
    assert result.caption == "Clinical caption"
    assert [comment.author for comment in result.comments] == ["Public Doctor", None]
    assert len(result.checksum_sha256) == 64
    persisted = repository.get_job(job_id)
    assert persisted.status is WorkflowStatus.DOWNLOADED
    assert persisted.data["video_path"] == str(video.resolve())
    assert persisted.data["checksum_sha256"] == result.checksum_sha256
    assert [event.to_status for event in repository.list_events(job_id)] == [
        WorkflowStatus.CREATED,
        WorkflowStatus.DOWNLOADREEL_RUNNING,
        WorkflowStatus.DOWNLOADED,
    ]


def test_source_url_is_written_to_normalized_metadata(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = make_repository(settings)
    video = make_video_and_metadata(tmp_path)
    adapter = DownloadReelAdapter(settings, repository, downloader=lambda _: legacy_result(video))
    result = asyncio.run(adapter.process(REEL_URL, create_job(repository)))

    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["source_url"] == REEL_URL
    assert metadata["normalized_source_url"] == NORMALIZED_URL
    assert metadata["normalized_comments"][1]["author"] is None


def test_missing_output_file_fails_and_persists_error(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = make_repository(settings)
    missing = tmp_path / "missing.mp4"
    adapter = DownloadReelAdapter(settings, repository, downloader=lambda _: legacy_result(missing))
    job_id = create_job(repository)

    result = asyncio.run(adapter.process(REEL_URL, job_id))

    assert not result.success
    assert "does not exist" in result.error
    assert repository.get_job(job_id).status is WorkflowStatus.DOWNLOADREEL_FAILED


def test_zero_byte_video_is_rejected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = make_repository(settings)
    video = make_video_and_metadata(tmp_path, content=b"")
    adapter = DownloadReelAdapter(settings, repository, downloader=lambda _: legacy_result(video))

    result = asyncio.run(adapter.process(REEL_URL, create_job(repository)))
    assert not result.success
    assert "empty" in result.error


def test_unsupported_video_extension_is_rejected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = make_repository(settings)
    video = make_video_and_metadata(tmp_path, name="video.txt")
    adapter = DownloadReelAdapter(settings, repository, downloader=lambda _: legacy_result(video))

    result = asyncio.run(adapter.process(REEL_URL, create_job(repository)))
    assert not result.success
    assert "Unsupported video extension" in result.error


def test_existing_valid_download_is_reused_without_calling_downloader(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = make_repository(settings)
    video = make_video_and_metadata(tmp_path)
    calls = 0

    def downloader(_: str) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return legacy_result(video)

    adapter = DownloadReelAdapter(settings, repository, downloader=downloader)
    coordinator = DownloadReelCoordinator(
        settings, repository, adapter, history_path=tmp_path / "missing-history.json"
    )
    first = asyncio.run(coordinator.run(REEL_URL))
    second = asyncio.run(coordinator.run("https://facebook.com/reel/123/"))

    assert first.success and second.success
    assert second.reused
    assert first.job_id == second.job_id
    assert calls == 1


def test_retry_transition_uses_same_failed_job(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = make_repository(settings)
    video = make_video_and_metadata(tmp_path)
    responses = iter(
        [
            legacy_result(video, status="error", error="temporary failure"),
            legacy_result(video),
        ]
    )
    adapter = DownloadReelAdapter(settings, repository, downloader=lambda _: next(responses))
    coordinator = DownloadReelCoordinator(
        settings, repository, adapter, history_path=tmp_path / "missing-history.json"
    )

    first = asyncio.run(coordinator.run(REEL_URL))
    retry = asyncio.run(coordinator.run(REEL_URL, force_download=True))

    assert not first.success and retry.success
    assert retry.job_id == first.job_id
    statuses = [event.to_status for event in repository.list_events(first.job_id)]
    assert WorkflowStatus.RETRY_PENDING in statuses
    assert statuses[-1] is WorkflowStatus.DOWNLOADED


def test_force_download_creates_new_job_after_terminal_job(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = make_repository(settings)
    video = make_video_and_metadata(tmp_path)
    calls = 0

    def downloader(_: str) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return legacy_result(video)

    adapter = DownloadReelAdapter(settings, repository, downloader=downloader)
    coordinator = DownloadReelCoordinator(
        settings, repository, adapter, history_path=tmp_path / "missing-history.json"
    )
    first = asyncio.run(coordinator.run(REEL_URL))
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE jobs SET status = ? WHERE job_id = ?",
            (WorkflowStatus.COMPLETED.value, first.job_id),
        )
        connection.commit()

    forced = asyncio.run(coordinator.run(REEL_URL, force_download=True))

    assert forced.success
    assert forced.job_id != first.job_id
    assert calls == 2


def test_async_adapter_does_not_block_event_loop(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = make_repository(settings)
    video = make_video_and_metadata(tmp_path)

    def slow_downloader(_: str) -> SimpleNamespace:
        time.sleep(0.15)
        return legacy_result(video)

    adapter = DownloadReelAdapter(settings, repository, downloader=slow_downloader)
    job_id = create_job(repository)

    async def scenario() -> tuple[bool, bool]:
        task = asyncio.create_task(adapter.process(REEL_URL, job_id))
        await asyncio.sleep(0.03)
        event_loop_progressed = not task.done()
        result = await task
        return event_loop_progressed, result.success

    assert asyncio.run(scenario()) == (True, True)
