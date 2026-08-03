from app.application.ports.browser_port import BrowserPort
from app.application.ports.job_repository_port import LegacyDispatchRepositoryPort
from app.domain.models.facebook_job import FacebookJob
from app.domain.models.job_result import JobResult

class CheckFacebookLoginUseCase:
    def __init__(self, browser_port: BrowserPort, job_repository: LegacyDispatchRepositoryPort):
        self._browser_port = browser_port
        self._job_repository = job_repository

    async def execute(self, job: FacebookJob) -> JobResult:
        self._job_repository.mark_running(job.job_id)
        try:
            connected = await self._browser_port.is_connected()
            data = {"is_connected": connected}
            self._job_repository.mark_success(job.job_id, data)
            return JobResult.success_result(job.job_id, data)
        except Exception as exc:
            self._job_repository.mark_failed(job.job_id, str(exc))
            return JobResult.failure_result(job.job_id, str(exc))
