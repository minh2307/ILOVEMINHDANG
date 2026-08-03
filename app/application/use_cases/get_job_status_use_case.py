from __future__ import annotations

import json

from app.application.ports.job_queue_port import JobQueuePort
from app.application.ports.job_repository_port import JobRepositoryPort
from app.domain.models.job_result import JobResult


class GetJobStatusUseCase:
    """Return the persisted aggregate and only its related queue work items."""

    def __init__(
        self, repository: JobRepositoryPort, queue: JobQueuePort
    ) -> None:
        self._repository = repository
        self._queue = queue

    async def execute(self, job_id: str) -> JobResult:
        job = self._repository.get_job(job_id)
        if job is None:
            return JobResult.failure_result(job_id, f"Job not found: {job_id}")
        related: list[dict] = []
        for record in await self._queue.list_records():
            try:
                payload = json.loads(record.get("payload") or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            if payload.get("workflow_job_id") == job.job_id:
                related.append(record)
        return JobResult.success_result(
            job_id,
            {
                "job": job.to_dict(),
                "events": [
                    event.to_dict() for event in self._repository.list_events(job.job_id)
                ],
                "queue_items": related,
            },
        )
