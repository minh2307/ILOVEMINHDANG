from typing import Dict
from app.domain.models.facebook_job import FacebookJob
from app.domain.models.job_result import JobResult
from app.domain.enums.facebook_job_type import FacebookJobType
from app.domain.exceptions.job_exception import UnsupportedJobTypeError
from typing import Protocol

class UseCase(Protocol):
    async def execute(self, job: FacebookJob) -> JobResult:
        ...

class FacebookJobDispatcher:
    def __init__(self, handlers: Dict[FacebookJobType, UseCase]):
        self._handlers = handlers

    async def dispatch(self, job: FacebookJob) -> JobResult:
        handler = self._handlers.get(job.job_type)
        if not handler:
            raise UnsupportedJobTypeError(f"Unsupported job type: {job.job_type}")
        return await handler.execute(job)
