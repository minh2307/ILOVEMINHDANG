from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass
class JobResult:
    job_id: str
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @classmethod
    def success_result(cls, job_id: str, data: Dict[str, Any]) -> "JobResult":
        return cls(job_id=job_id, success=True, data=data)

    @classmethod
    def failure_result(cls, job_id: str, error: str) -> "JobResult":
        return cls(job_id=job_id, success=False, error=error)
