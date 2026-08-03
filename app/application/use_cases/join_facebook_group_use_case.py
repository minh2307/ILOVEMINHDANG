from app.application.ports.facebook_group_port import FacebookGroupPort
from app.application.ports.job_repository_port import LegacyDispatchRepositoryPort
from app.domain.models.facebook_job import FacebookJob
from app.domain.models.job_result import JobResult

class JoinFacebookGroupUseCase:
    def __init__(self, group_port: FacebookGroupPort, job_repository: LegacyDispatchRepositoryPort):
        self._group_port = group_port
        self._job_repository = job_repository

    async def execute(self, job: FacebookJob) -> JobResult:
        self._job_repository.mark_running(job.job_id)
        try:
            url = job.payload.get("group_url")
            if not url:
                raise ValueError("group_url is required in payload")
            data = await self._group_port.join_group(url)
            self._job_repository.mark_success(job.job_id, data)
            return JobResult.success_result(job.job_id, data)
        except Exception as exc:
            self._job_repository.mark_failed(job.job_id, str(exc))
            return JobResult.failure_result(job.job_id, str(exc))
