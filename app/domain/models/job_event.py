from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.enums.job_status import JobStatus


@dataclass(slots=True)
class JobEvent:
    event_id: int
    job_id: str
    from_status: JobStatus | None
    to_status: JobStatus
    details: dict[str, Any]
    created_at: str
    event_type: str = "JOB_STATE_CHANGED"
    attempt: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "job_id": self.job_id,
            "from_status": self.from_status.value if self.from_status else None,
            "to_status": self.to_status.value,
            "event_type": self.event_type,
            "attempt": self.attempt,
            "details": self.details,
            "created_at": self.created_at,
        }
