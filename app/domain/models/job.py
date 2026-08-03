from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.enums.job_status import JobStatus
from app.domain.enums.job_type import JobType


@dataclass(slots=True)
class Job:
    """Authoritative persisted workflow job.

    The first seven fields preserve the historical ``JobRecord`` positional
    contract. Operational metadata is explicit so recovery does not depend on
    opaque JSON fields.
    """

    job_id: str
    source_url: str
    status: JobStatus
    normalized_source_url: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    job_type: JobType = JobType.PROCESS_WORKFLOW
    previous_status: JobStatus | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    output_payload: dict[str, Any] = field(default_factory=dict)
    artifact_paths: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    attempt_count: int = 0
    max_attempts: int = 3
    claimed_by: str | None = None
    lease_expires_at: str | None = None
    last_heartbeat: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type.value,
            "source_url": self.source_url,
            "normalized_source_url": self.normalized_source_url,
            "status": self.status.value,
            "previous_status": self.previous_status.value if self.previous_status else None,
            "input_payload": self.input_payload,
            "output_payload": self.output_payload,
            "artifact_paths": list(self.artifact_paths),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "claimed_by": self.claimed_by,
            "lease_expires_at": self.lease_expires_at,
            "last_heartbeat": self.last_heartbeat,
            "completed_at": self.completed_at,
            "data": self.data,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


JobRecord = Job
