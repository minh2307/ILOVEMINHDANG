from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.models.results import DownloadResult
from app.models.workflow import JobRecord, WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.reel_normalization import (
    normalize_caption,
    normalize_comments,
    normalize_reel_url,
    normalized_source_identities,
    original_source_url,
)


SUPPORTED_VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov"})
TERMINAL_STATUSES = {
    WorkflowStatus.COMPLETED,
    WorkflowStatus.REJECTED,
    WorkflowStatus.FAILED,
    WorkflowStatus.DOWNLOADREEL_FAILED,
}


class DownloadValidationError(RuntimeError):
    pass


class DuplicateDownloadError(RuntimeError):
    pass


class DownloadReelAdapter:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        downloader: Callable[[str], Any] | None = None,
        logger: logging.Logger | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self._downloader = downloader
        self._download_authentication_method = (
            "injected_test_double" if downloader is not None else "not_checked"
        )
        self.logger = logger or logging.getLogger("cdha_pipeline.downloadreel")

    def _get_downloader(self) -> Callable[[str], Any]:
        if self._downloader is not None:
            return self._downloader
        legacy_dir = str(self.settings.downloadreel_dir)
        if legacy_dir not in sys.path:
            sys.path.insert(0, legacy_dir)
        inspection = self.settings.inspect_facebook_cookie()
        self._download_authentication_method = inspection.authentication_method
        cookie_path = inspection.path if inspection.valid else None
        self.logger.info(
            "Facebook Reel cookie configuration",
            extra={
                "component": "downloadreel",
                "event": "COOKIE_CONFIGURATION",
                "details": {
                    "path": str(inspection.path),
                    "status": inspection.status,
                    "authentication_method": inspection.authentication_method,
                },
            },
        )
        module = importlib.import_module("fb_downloader")
        self._downloader = partial(
            module.process_and_download_reel,
            scrape_browser_metadata=False,
            cookie_path=cookie_path,
        )
        return self._downloader

    async def process(self, reel_url: str, job_id: str) -> DownloadResult:
        source_url = original_source_url(reel_url)
        normalized_url = normalize_reel_url(reel_url)
        job = self.repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        if job.normalized_source_url and job.normalized_source_url != normalized_url:
            raise ValueError("Job normalized source does not match the requested Reel")

        started_at = datetime.now(UTC).isoformat()
        self.repository.transition(
            job_id,
            WorkflowStatus.DOWNLOADREEL_RUNNING,
            details={"source_url": source_url, "normalized_source_url": normalized_url},
            data_patch={
                "source_url": source_url,
                "normalized_source_url": normalized_url,
                "download_started_at": started_at,
                "error": None,
            },
        )
        try:
            legacy_result = await asyncio.to_thread(self._get_downloader(), normalized_url)
            if getattr(legacy_result, "status", None) != "done":
                error = getattr(legacy_result, "error_msg", "") or "DownloadReel reported failure"
                raise DownloadValidationError(error)
            result = await asyncio.to_thread(
                self._normalize_legacy_result,
                legacy_result,
                job_id,
                source_url,
                normalized_url,
                False,
            )
            completed_at = datetime.now(UTC).isoformat()
            self.repository.transition(
                job_id,
                WorkflowStatus.DOWNLOADED,
                details={"video_path": str(result.video_path), "reused": False},
                data_patch=self._result_data(result, started_at, completed_at),
            )
            return result
        except Exception as exc:
            completed_at = datetime.now(UTC).isoformat()
            error = str(exc) or type(exc).__name__
            self.repository.transition(
                job_id,
                WorkflowStatus.DOWNLOADREEL_FAILED,
                details={"error": error},
                data_patch={
                    "source_url": source_url,
                    "normalized_source_url": normalized_url,
                    "error": error,
                    "download_started_at": started_at,
                    "download_completed_at": completed_at,
                    "download_authentication_method": self._download_authentication_method,
                },
            )
            self.logger.error("DownloadReel step failed", extra={"job_id": job_id, "error": error})
            return self.failure_result(job_id, source_url, normalized_url, error)

    async def reuse_existing_file(
        self,
        reel_url: str,
        job_id: str,
        video_path: Path,
        downloaded_at: str = "",
    ) -> DownloadResult:
        source_url = original_source_url(reel_url)
        normalized_url = normalize_reel_url(reel_url)
        started_at = datetime.now(UTC).isoformat()
        self.repository.transition(
            job_id,
            WorkflowStatus.DOWNLOADREEL_RUNNING,
            details={"reason": "reuse_downloader_history"},
            data_patch={
                "source_url": source_url,
                "normalized_source_url": normalized_url,
                "download_started_at": started_at,
            },
        )
        try:
            result = await asyncio.to_thread(
                self._normalize_paths,
                Path(video_path),
                job_id,
                source_url,
                normalized_url,
                downloaded_at,
                True,
            )
            completed_at = datetime.now(UTC).isoformat()
            self.repository.transition(
                job_id,
                WorkflowStatus.DOWNLOADED,
                details={"video_path": str(result.video_path), "reused": True},
                data_patch=self._result_data(result, started_at, completed_at),
            )
            return result
        except Exception as exc:
            error = f"Downloader history is inconsistent: {exc}"
            completed_at = datetime.now(UTC).isoformat()
            self.repository.transition(
                job_id,
                WorkflowStatus.DOWNLOADREEL_FAILED,
                details={"error": error},
                data_patch={
                    "error": error,
                    "download_started_at": started_at,
                    "download_completed_at": completed_at,
                },
            )
            return self.failure_result(job_id, source_url, normalized_url, error)

    async def result_from_job(self, job: JobRecord) -> DownloadResult:
        video_path = job.data.get("video_path")
        if not video_path:
            raise DownloadValidationError("Previous job has no recorded video path")
        return await asyncio.to_thread(
            self._normalize_paths,
            Path(video_path),
            job.job_id,
            job.source_url,
            job.normalized_source_url,
            str(job.data.get("downloaded_at") or ""),
            True,
        )

    def _normalize_legacy_result(
        self,
        legacy_result: Any,
        job_id: str,
        source_url: str,
        normalized_url: str,
        reused: bool,
    ) -> DownloadResult:
        raw_path = getattr(legacy_result, "file_path", "")
        if not raw_path:
            raise DownloadValidationError("DownloadReel returned no video path")
        return self._normalize_paths(
            Path(raw_path),
            job_id,
            source_url,
            normalized_url,
            str(getattr(legacy_result, "downloaded_at", "") or ""),
            reused,
        )

    def _normalize_paths(
        self,
        video_path: Path,
        job_id: str,
        source_url: str,
        normalized_url: str,
        downloaded_at: str,
        reused: bool,
    ) -> DownloadResult:
        video = self._validate_video(video_path)
        metadata_path = video.with_suffix(".json").resolve()
        if not metadata_path.exists() or not metadata_path.is_file():
            raise DownloadValidationError(f"Metadata sidecar does not exist: {metadata_path}")
        if metadata_path.stat().st_size <= 0:
            raise DownloadValidationError(f"Metadata sidecar is empty: {metadata_path}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DownloadValidationError(f"Metadata sidecar is invalid: {exc}") from exc
        if not isinstance(metadata, dict):
            raise DownloadValidationError("Metadata sidecar must contain a JSON object")

        comments_source = metadata.get("commentDetails") or metadata.get("normalized_comments")
        if not isinstance(comments_source, list) or not comments_source:
            comments_source = metadata.get("comments") or []
        comments = normalize_comments(comments_source)
        caption = normalize_caption(metadata.get("caption"))
        effective_downloaded_at = (
            downloaded_at
            or str(metadata.get("downloaded_at") or "")
            or datetime.fromtimestamp(video.stat().st_mtime, UTC).isoformat()
        )
        metadata.update(
            {
                "source_url": source_url,
                "normalized_source_url": normalized_url,
                "video": video.name,
                "caption": caption,
                "normalized_comments": [comment.to_dict() for comment in comments],
                "downloaded_at": effective_downloaded_at,
            }
        )
        self._write_json_atomic(metadata_path, metadata)
        checksum = self._sha256(video)
        return DownloadResult(
            job_id=job_id,
            source_url=source_url,
            normalized_source_url=normalized_url,
            video_path=video,
            video_filename=video.name,
            video_size_bytes=video.stat().st_size,
            caption=caption,
            comments=comments,
            metadata_path=metadata_path,
            downloaded_at=effective_downloaded_at,
            checksum_sha256=checksum,
            success=True,
            error=None,
            reused=reused,
        )

    @staticmethod
    def _validate_video(path: Path) -> Path:
        video = Path(path).expanduser().resolve()
        if not video.exists():
            raise DownloadValidationError(f"Video file does not exist: {video}")
        if not video.is_file():
            raise DownloadValidationError(f"Video path is not a regular file: {video}")
        if video.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise DownloadValidationError(f"Unsupported video extension: {video.suffix or '<none>'}")
        if video.stat().st_size <= 0:
            raise DownloadValidationError(f"Video file is empty: {video}")
        return video

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".adapter.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(path)
            verified = json.loads(path.read_text(encoding="utf-8"))
            if verified.get("normalized_source_url") != payload["normalized_source_url"]:
                raise DownloadValidationError(f"Metadata verification failed: {path}")
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _result_data(self, result: DownloadResult, started_at: str, completed_at: str) -> dict[str, Any]:
        return {
            "source_url": result.source_url,
            "normalized_source_url": result.normalized_source_url,
            "video_path": str(result.video_path),
            "video_size_bytes": result.video_size_bytes,
            "checksum_sha256": result.checksum_sha256,
            "caption": result.caption,
            "comments": [comment.to_dict() for comment in result.comments],
            "metadata_path": str(result.metadata_path),
            "downloaded_at": result.downloaded_at,
            "download_started_at": started_at,
            "download_completed_at": completed_at,
            "error": None,
            "reused_download": result.reused,
            "download_authentication_method": self._download_authentication_method,
        }

    @staticmethod
    def failure_result(
        job_id: str, source_url: str, normalized_url: str, error: str
    ) -> DownloadResult:
        return DownloadResult(
            job_id=job_id,
            source_url=source_url,
            normalized_source_url=normalized_url,
            success=False,
            error=error,
        )


class DownloadReelCoordinator:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        adapter: DownloadReelAdapter,
        history_path: Path | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.adapter = adapter
        self.history_path = history_path or settings.downloadreel_dir / "download_log.json"

    async def run(self, reel_url: str, *, force_download: bool = False) -> DownloadResult:
        source_url = original_source_url(reel_url)
        normalized_url = normalize_reel_url(reel_url)
        previous = self.repository.find_latest_by_normalized_source_url(normalized_url)
        if previous is not None:
            if previous.status is WorkflowStatus.DOWNLOADREEL_FAILED:
                if not force_download:
                    return self.adapter.failure_result(
                        previous.job_id,
                        source_url,
                        normalized_url,
                        "Previous download failed; use --force-download to retry explicitly",
                    )
                self.repository.transition(
                    previous.job_id,
                    WorkflowStatus.RETRY_PENDING,
                    details={"reason": "operator_force_retry"},
                )
                return await self.adapter.process(reel_url, previous.job_id)

            if previous.status in {WorkflowStatus.CREATED, WorkflowStatus.RETRY_PENDING}:
                return await self.adapter.process(reel_url, previous.job_id)

            try:
                reusable = await self.adapter.result_from_job(previous)
            except DownloadValidationError as exc:
                if previous.status not in TERMINAL_STATUSES:
                    return self.adapter.failure_result(
                        previous.job_id,
                        source_url,
                        normalized_url,
                        f"Previous job is still active or inconsistent: {exc}",
                    )
                if not force_download:
                    return self.adapter.failure_result(
                        previous.job_id,
                        source_url,
                        normalized_url,
                        f"Previous job metadata is inconsistent: {exc}",
                    )
            else:
                if not force_download or previous.status not in TERMINAL_STATUSES:
                    return replace(reusable, reused=True)

        if not force_download:
            history_rows = self._matching_history(normalized_url)
            if history_rows:
                history = history_rows[-1]
                job = self.repository.create_job(
                    source_url,
                    normalized_source_url=normalized_url,
                    data={"history_import": True},
                )
                return await self.adapter.reuse_existing_file(
                    reel_url,
                    job.job_id,
                    Path(str(history.get("file_path") or "")),
                    str(history.get("downloaded_at") or ""),
                )

        job = self.repository.create_job(source_url, normalized_source_url=normalized_url)
        return await self.adapter.process(reel_url, job.job_id)

    def _matching_history(self, normalized_url: str) -> list[dict[str, Any]]:
        if not self.history_path.is_file():
            return []
        try:
            rows = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(rows, list):
            return []
        return [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("status") == "done"
            and normalized_url in normalized_source_identities(str(row.get("url") or ""))
        ]
