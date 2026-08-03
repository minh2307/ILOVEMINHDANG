import pytest
import asyncio
from app.application.use_cases.download_reel_use_case import DownloadReelUseCase
from app.domain.models.facebook_job import FacebookJob
from app.domain.enums.facebook_job_type import FacebookJobType

class MockReelPort:
    async def download_reel(self, url: str):
        return {"url": url, "status": "mocked_download"}
    
    async def extract_metadata(self, url: str):
        return {}

class MockJobRepo:
    def mark_running(self, job_id): pass
    def mark_success(self, job_id, data=None): pass
    def mark_failed(self, job_id, error): pass
    def get_job(self, job_id): return None

@pytest.mark.asyncio
async def test_download_reel_use_case():
    port = MockReelPort()
    repo = MockJobRepo()
    use_case = DownloadReelUseCase(port, repo)
    use_case.is_download_complete = lambda u: "mocked_path.mp4"
    
    job = FacebookJob(job_id="1", job_type=FacebookJobType.DOWNLOAD_REEL, payload={"url": "http://test"})
    result = await use_case.execute(job)
    
    assert result.success is True
    assert result.data["url"] == "http://test"
