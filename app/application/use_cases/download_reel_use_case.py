import re
import os
from pathlib import Path
from app.application.ports.facebook_reel_port import FacebookReelPort
from app.application.ports.job_repository_port import LegacyDispatchRepositoryPort
from app.domain.models.facebook_job import FacebookJob
from app.domain.models.job_result import JobResult
import logging

logger = logging.getLogger(__name__)

class DownloadReelUseCase:
    def __init__(self, reel_port: FacebookReelPort, job_repository: LegacyDispatchRepositoryPort):
        self._reel_port = reel_port
        self._job_repository = job_repository
        
    def is_download_complete(self, url: str) -> str | None:
        # Extract Facebook video/reel ID from URL
        match = re.search(r'/(?:reel|video)s?/(?:[a-zA-Z0-9.]+/?v=)?(\d+)', url)
        if not match:
            # Fallback for generic IDs
            match = re.search(r'(\d+)', url)
        if not match:
            return None
            
        video_id = match.group(1)
        output_dir = Path("runtime/downloads")
        if not output_dir.exists():
            return None
            
        for file_path in output_dir.glob(f"*{video_id}*.*"):
            # Ignore temporary files
            if file_path.suffix in {".part", ".ytdl", ".tmp", ".crdownload"}:
                continue
            if file_path.stat().st_size > 0:
                return str(file_path)
        return None

    async def execute(self, job: FacebookJob) -> JobResult:
        self._job_repository.mark_running(job.job_id)
        try:
            url = job.payload.get("url")
            if not url:
                raise ValueError("URL is required in payload")
                
            existing_file = self.is_download_complete(url)
            if existing_file:
                logger.info("Download output already exists and is valid. Skipping download.")
                data = {
                    "url": url,
                    "status": "SKIPPED_ALREADY_EXISTS",
                    "video_path": existing_file
                }
                self._job_repository.mark_success(job.job_id, data)
                return JobResult.success_result(job.job_id, data)
                
            data = await self._reel_port.download_reel(url)
            
            filepath = data.get("video_path")
            if not filepath or not Path(filepath).exists():
                # Yt-dlp might not return filepath, or it has wrong extension, double check
                found = self.is_download_complete(url)
                if found:
                    data["video_path"] = found
                else:
                    raise FileNotFoundError("Video was downloaded but file not found on disk")
                    
            self._job_repository.mark_success(job.job_id, data)
            return JobResult.success_result(job.job_id, data)
        except Exception as exc:
            self._job_repository.mark_failed(job.job_id, str(exc))
            return JobResult.failure_result(job.job_id, str(exc))
