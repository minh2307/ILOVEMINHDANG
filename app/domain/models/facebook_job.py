from dataclasses import dataclass, field
from typing import Any, Dict
from app.domain.enums.facebook_job_type import FacebookJobType
from app.domain.enums.job_status import JobStatus

@dataclass
class FacebookJob:
    job_id: str
    job_type: FacebookJobType
    payload: Dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.CREATED
    attempt_count: int = 0
    max_attempts: int = 10
    next_retry_at: float = 0.0
