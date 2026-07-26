from __future__ import annotations

import asyncio
import importlib
import json
import os
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapters.downloadreel_adapter import DownloadReelAdapter, DownloadReelCoordinator
from app.config.settings import Settings
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.reel_normalization import (
    MultipleReelUrlsError,
    normalize_caption,
    normalize_comments,
    normalize_reel_url,
)


ROOT = Path(__file__).resolve().parents[1]
LEGACY_DIR = ROOT / "dowloadReelFB"
if str(LEGACY_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_DIR))


def settings_for(tmp_path: Path) -> Settings:
    return replace(
        Settings.from_env(env_file=tmp_path / "missing.env"),
        database_path=tmp_path / "jobs.sqlite3",
        job_data_dir=tmp_path / "jobs",
        log_dir=tmp_path / "logs",
        screenshot_dir=tmp_path / "screenshots",
        chrome_profile_dir=tmp_path / "profile",
        downloadreel_dir=LEGACY_DIR,
    )


def repository_for(settings: Settings) -> JobRepository:
    repository = JobRepository(settings.database_path)
    repository.initialize()
    return repository


def write_asset(tmp_path: Path, name: str = "history.mp4") -> Path:
    video = tmp_path / name
    video.write_bytes(b"valid-video")
    video.with_suffix(".json").write_text(
        json.dumps({"video": video.name, "caption": "Caption", "comments": ["Finding"]}),
        encoding="utf-8",
    )
    return video


@pytest.mark.parametrize(
    "value",
    [
        "https://www.facebook.com/reel/123/",
        "https://facebook.com/reel/123",
        " https://www.facebook.com/reel/123/?mibextid=abc#fragment ",
        "https://m.facebook.com/reel/123/?tracking=1",
    ],
)
def test_url_normalization(value: str) -> None:
    assert normalize_reel_url(value) == "https://www.facebook.com/reel/123"


def test_share_url_normalization() -> None:
    assert normalize_reel_url(
        "https://www.facebook.com/share/r/AbCdEf/?mibextid=test"
    ) == "https://www.facebook.com/share/r/AbCdEf"


def test_duplicate_newline_urls_are_accepted_but_different_urls_are_rejected() -> None:
    duplicate_lines = (
        "https://www.facebook.com/reel/123/\n"
        "https://facebook.com/reel/123?mibextid=same"
    )
    assert normalize_reel_url(duplicate_lines) == "https://www.facebook.com/reel/123"
    with pytest.raises(MultipleReelUrlsError):
        normalize_reel_url(
            "https://facebook.com/reel/123\nhttps://facebook.com/reel/456"
        )


def test_caption_and_comment_normalization() -> None:
    assert normalize_caption("  First line  \r\n\r\nSecond line\nXem thêm") == (
        "First line\n\nSecond line"
    )
    comments = normalize_comments(
        [
            "Like",
            "",
            "Visible comment\nSee more",
            "Visible comment",
            {"author": "Public Name", "content": "Second comment"},
            {"author": "", "content": "Third comment"},
        ]
    )
    assert [comment.content for comment in comments] == [
        "Visible comment",
        "Second comment",
        "Third comment",
    ]
    assert [comment.author for comment in comments] == [None, "Public Name", None]


def test_legacy_duplicate_checker_uses_identity_with_malformed_history(tmp_path: Path) -> None:
    duplicate_checker = importlib.import_module("duplicate_checker")
    history = tmp_path / "download_log.json"
    history.write_text(
        json.dumps(
            [
                {
                    "url": (
                        "https://facebook.com/reel/123?tracking=1\n"
                        "https://facebook.com/reel/456/"
                    ),
                    "status": "done",
                    "file_path": "/old/missing/path.mp4",
                }
            ]
        ),
        encoding="utf-8",
    )
    checker = duplicate_checker.DuplicateChecker(history)

    assert checker.is_duplicate("https://www.facebook.com/reel/123/")
    assert checker.is_duplicate("https://www.facebook.com/reel/456?mibextid=x")


def test_valid_history_is_reused_without_downloader_call(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    repository = repository_for(settings)
    video = write_asset(tmp_path)
    history = tmp_path / "download_log.json"
    history.write_text(
        json.dumps(
            [
                {
                    "url": "https://facebook.com/reel/123/?mibextid=x",
                    "status": "done",
                    "file_path": str(video),
                    "downloaded_at": "2026-07-20T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    def must_not_run(_: str) -> object:
        raise AssertionError("downloader should not run")

    adapter = DownloadReelAdapter(settings, repository, downloader=must_not_run)
    coordinator = DownloadReelCoordinator(settings, repository, adapter, history_path=history)
    result = asyncio.run(coordinator.run("https://www.facebook.com/reel/123"))

    assert result.success and result.reused
    assert repository.get_job(result.job_id).status is WorkflowStatus.DOWNLOADED


def test_missing_historical_file_is_reported_as_inconsistent(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    repository = repository_for(settings)
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps(
            [
                {
                    "url": "https://facebook.com/reel/123",
                    "status": "done",
                    "file_path": str(tmp_path / "missing.mp4"),
                }
            ]
        ),
        encoding="utf-8",
    )
    adapter = DownloadReelAdapter(settings, repository, downloader=lambda _: None)
    coordinator = DownloadReelCoordinator(settings, repository, adapter, history_path=history)

    result = asyncio.run(coordinator.run("https://facebook.com/reel/123"))

    assert not result.success
    assert "history is inconsistent" in result.error
    assert repository.get_job(result.job_id).status is WorkflowStatus.DOWNLOADREEL_FAILED


def test_yt_dlp_no_metadata_is_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    download_manager = importlib.import_module("download_manager")

    class FakeYDL:
        def __init__(self, options: dict):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url: str, download: bool):
            return None

    class FakeMetadata:
        def add_record(self, *args):
            raise AssertionError("metadata must not be recorded")

    monkeypatch.setattr(download_manager.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(download_manager, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(download_manager, "COOKIES_FILE", tmp_path / "missing-cookies.txt")
    video = download_manager.VideoInfo(url="https://facebook.com/reel/123")

    result = download_manager.download_single(video, metadata_mgr=FakeMetadata())

    assert result.status == "error"
    assert "no metadata" in result.error_msg


def test_no_output_file_is_not_marked_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    download_manager = importlib.import_module("download_manager")

    class FakeYDL:
        def __init__(self, options: dict):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url: str, download: bool):
            return {"id": "123", "title": "Missing"}

        def prepare_filename(self, info: dict):
            return str(tmp_path / "missing.mp4")

    monkeypatch.setattr(download_manager.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(download_manager, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(download_manager, "COOKIES_FILE", tmp_path / "missing-cookies.txt")
    result = download_manager.download_single(download_manager.VideoInfo(url="https://facebook.com/reel/123"))

    assert result.status == "error"
    assert "No valid downloaded video" in result.error_msg


def test_sidecar_write_failure_marks_existing_process_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fb_downloader = importlib.import_module("fb_downloader")
    video = write_asset(tmp_path)
    successful = SimpleNamespace(
        status="done",
        file_path=str(video),
        error_msg="",
        downloaded_at="2026-07-22T00:00:00+00:00",
    )
    monkeypatch.setattr(fb_downloader, "download_single", lambda *args, **kwargs: successful)
    monkeypatch.setattr(
        fb_downloader.ReelScraper,
        "scrape",
        lambda self, url: {"caption": "Caption", "comments": [], "commentDetails": []},
    )
    monkeypatch.setattr(
        fb_downloader,
        "write_metadata_sidecar",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    result = fb_downloader.process_and_download_reel("https://facebook.com/reel/123")

    assert result.status == "error"
    assert "sidecar" in result.error_msg


def test_existing_process_writes_source_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fb_downloader = importlib.import_module("fb_downloader")
    video = write_asset(tmp_path)
    successful = SimpleNamespace(
        status="done",
        file_path=str(video),
        error_msg="",
        downloaded_at="2026-07-22T00:00:00+00:00",
    )
    monkeypatch.setattr(fb_downloader, "download_single", lambda *args, **kwargs: successful)
    monkeypatch.setattr(
        fb_downloader.ReelScraper,
        "scrape",
        lambda self, url: {"caption": "Caption", "comments": [], "commentDetails": []},
    )

    result = fb_downloader.process_and_download_reel("https://facebook.com/reel/123?track=1")
    metadata = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))

    assert result.status == "done"
    assert metadata["source_url"] == "https://www.facebook.com/reel/123"


def test_likes_are_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_REEL_LIKE", raising=False)
    monkeypatch.delenv("ENABLE_COMMENT_LIKE", raising=False)
    legacy_config = importlib.import_module("config")
    legacy_config = importlib.reload(legacy_config)
    assert legacy_config.ENABLE_REEL_LIKE is False
    assert legacy_config.ENABLE_POST_LIKE is False
    assert legacy_config.ENABLE_COMMENT_LIKE is False


class FakeMetadataManager:
    def load(self) -> list:
        return []

    def remove_record(self, filename: str) -> None:
        pass


def make_old(path: Path) -> None:
    old = time_value = 1_600_000_000
    os.utime(path, (old, time_value))


def test_active_job_assets_are_protected_from_cleanup(tmp_path: Path) -> None:
    cleanup_manager = importlib.import_module("cleanup_manager")
    settings = settings_for(tmp_path)
    repository = repository_for(settings)
    video = write_asset(tmp_path)
    sidecar = video.with_suffix(".json")
    make_old(video)
    make_old(sidecar)
    repository.create_job(
        "https://facebook.com/reel/123",
        normalized_source_url="https://www.facebook.com/reel/123",
        data={"video_path": str(video), "metadata_path": str(sidecar)},
    )

    cleanup_manager.CleanupManager.cleanup(
        tmp_path,
        FakeMetadataManager(),
        max_age_hours=1,
        jobs_database_path=settings.database_path,
    )

    assert video.exists() and sidecar.exists()


def test_completed_expired_assets_are_cleanup_eligible(tmp_path: Path) -> None:
    cleanup_manager = importlib.import_module("cleanup_manager")
    settings = settings_for(tmp_path)
    repository = repository_for(settings)
    video = write_asset(tmp_path)
    sidecar = video.with_suffix(".json")
    make_old(video)
    make_old(sidecar)
    job = repository.create_job(
        "https://facebook.com/reel/123",
        normalized_source_url="https://www.facebook.com/reel/123",
        data={"video_path": str(video), "metadata_path": str(sidecar)},
    )
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE jobs SET status = ? WHERE job_id = ?",
            (WorkflowStatus.COMPLETED.value, job.job_id),
        )
        connection.commit()

    cleanup_manager.CleanupManager.cleanup(
        tmp_path,
        FakeMetadataManager(),
        max_age_hours=1,
        jobs_database_path=settings.database_path,
    )

    assert not video.exists()
    assert not sidecar.exists()
