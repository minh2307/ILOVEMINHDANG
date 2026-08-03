from app.application.ports.facebook_reel_port import FacebookReelPort
from app.application.ports.job_repository_port import LegacyDispatchRepositoryPort
from app.domain.models.facebook_job import FacebookJob
from app.domain.models.job_result import JobResult

class ExtractReelMetadataUseCase:
    def __init__(self, reel_port: FacebookReelPort, job_repository: LegacyDispatchRepositoryPort):
        self._reel_port = reel_port
        self._job_repository = job_repository

    async def execute(self, job: FacebookJob) -> JobResult:
        self._job_repository.mark_running(job.job_id)
        try:
            url = job.payload.get("url")
            if not url:
                raise ValueError("URL is required in payload")
            data = await self._reel_port.extract_metadata(url)
            self._job_repository.mark_success(job.job_id, data)
            return JobResult.success_result(job.job_id, data)
        except Exception as exc:
            self._job_repository.mark_failed(job.job_id, str(exc))
            return JobResult.failure_result(job.job_id, str(exc))
