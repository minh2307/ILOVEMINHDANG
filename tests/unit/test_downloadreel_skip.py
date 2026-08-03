import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.application.use_cases.download_reel_use_case import DownloadReelUseCase
from app.domain.models.facebook_job import FacebookJob
from app.domain.enums.facebook_job_type import FacebookJobType

@pytest.fixture
def use_case():
    reel_port = AsyncMock()
    job_repository = MagicMock()
    return DownloadReelUseCase(reel_port, job_repository)

@pytest.mark.asyncio
async def test_skip_download_if_file_exists(use_case, tmp_path, monkeypatch):
    monkeypatch.setattr("app.application.use_cases.download_reel_use_case.Path", lambda p: tmp_path if p == "runtime/downloads" else Path(p))
    
    # Create fake download file
    video_id = "1575005554153911"
    fake_file = tmp_path / f"20231010_{video_id}_TestTitle.mp4"
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.write_text("fake video content")
    
    job = FacebookJob(
        job_id="job_123",
        job_type=FacebookJobType.DOWNLOAD_REEL,
        payload={"url": f"https://www.facebook.com/reel/{video_id}"}
    )
    
    result = await use_case.execute(job)
    
    assert result.success is True
    assert result.data["status"] == "SKIPPED_ALREADY_EXISTS"
    assert result.data["video_path"] == str(fake_file)
    use_case._reel_port.download_reel.assert_not_called()
    use_case._job_repository.mark_success.assert_called_once()
