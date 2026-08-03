from app.application.ports.facebook_post_port import FacebookPostPort
from app.application.ports.job_repository_port import LegacyDispatchRepositoryPort
from app.domain.models.facebook_job import FacebookJob
from app.domain.models.job_result import JobResult

class ShareFacebookPostUseCase:
    def __init__(self, post_port: FacebookPostPort, job_repository: LegacyDispatchRepositoryPort):
        self._post_port = post_port
        self._job_repository = job_repository

    async def execute(self, job: FacebookJob) -> JobResult:
        self._job_repository.mark_running(job.job_id)
        try:
            url = job.payload.get("post_url")
            if not url:
                raise ValueError("post_url is required in payload")
            data = await self._post_port.share_post(url)
            self._job_repository.mark_success(job.job_id, data)
            return JobResult.success_result(job.job_id, data)
        except Exception as exc:
            self._job_repository.mark_failed(job.job_id, str(exc))
            return JobResult.failure_result(job.job_id, str(exc))
