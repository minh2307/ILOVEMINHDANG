from pathlib import Path
from app.application.ports.facebook_post_port import FacebookPostPort
from app.application.ports.job_repository_port import LegacyDispatchRepositoryPort
from app.domain.models.facebook_job import FacebookJob
from app.domain.models.job_result import JobResult

class CreateFacebookPostUseCase:
    def __init__(self, post_port: FacebookPostPort, job_repository: LegacyDispatchRepositoryPort):
        self._post_port = post_port
        self._job_repository = job_repository

    async def execute(self, job: FacebookJob) -> JobResult:
        self._job_repository.mark_running(job.job_id)
        try:
            content = job.payload.get("content")
            if not content:
                raise ValueError("Content is required in payload")
            images = job.payload.get("images", [])
            image_paths = [Path(img) for img in images]
            data = await self._post_port.create_post(content, image_paths, job_id=job.job_id)
            self._job_repository.mark_success(job.job_id, data)
            return JobResult.success_result(job.job_id, data)
        except Exception as exc:
            self._job_repository.mark_failed(job.job_id, str(exc))
            return JobResult.failure_result(job.job_id, str(exc))
