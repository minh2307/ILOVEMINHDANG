from __future__ import annotations

import pytest

from app.application.use_cases.create_job_use_case import CreateJobUseCase
from app.application.use_cases.retry_job_use_case import RetryJobUseCase
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.domain.enums.job_status import JobStatus
from app.infrastructure.persistence.sqlite_job_queue import SQLiteJobQueue
from app.infrastructure.persistence.sqlite_job_repository import JobRepository


@pytest.fixture
def lifecycle(tmp_path):
    database = tmp_path / "application.sqlite3"
    repository = JobRepository(database)
    repository.initialize()
    queue = SQLiteJobQueue(str(database))
    scheduler = ScheduleWorkflowJobsUseCase(repository, queue)
    return repository, queue, scheduler


@pytest.mark.asyncio
async def test_create_job_normalizes_and_reuses_duplicate_unless_forced(lifecycle):
    repository, queue, scheduler = lifecycle
    use_case = CreateJobUseCase(repository, scheduler)

    first = await use_case.execute("https://www.facebook.com/reel/123/?tracking=1")
    duplicate = await use_case.execute("https://facebook.com/reel/123")
    forced = await use_case.execute("https://facebook.com/reel/123", force=True)

    assert first.success and first.data["queued"] is True
    assert duplicate.job_id == first.job_id
    assert duplicate.data == {
        "workflow_status": "CREATED", "reused": True, "queued": False,
    }
    assert forced.job_id != first.job_id
    assert len(repository.list_jobs()) == 2
    assert len(await queue.list_records()) == 2


@pytest.mark.asyncio
async def test_retry_job_persists_retry_step_before_queueing(lifecycle):
    repository, queue, scheduler = lifecycle
    repository.create_job("https://facebook.com/reel/456", job_id="job-retry")
    repository.transition("job-retry", JobStatus.DOWNLOADREEL_RUNNING)
    repository.transition(
        "job-retry",
        JobStatus.DOWNLOADREEL_FAILED,
        details={"error": "temporary network failure"},
    )
    use_case = RetryJobUseCase(repository, scheduler)

    result = await use_case.execute("job-retry")

    assert result.success is True
    persisted = repository.get_job("job-retry")
    assert persisted.status is JobStatus.RETRY_PENDING
    assert persisted.data["retry_step"] == "download"
    records = await queue.list_records()
    assert [record["job_id"] for record in records] == ["job-retry:RETRY_PENDING:attempt-1"]
