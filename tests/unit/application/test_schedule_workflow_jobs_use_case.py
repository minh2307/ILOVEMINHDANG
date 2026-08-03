from __future__ import annotations

import pytest

from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.domain.enums.job_status import JobStatus
from app.infrastructure.persistence.sqlite_job_queue import SQLiteJobQueue
from app.repositories.job_repository import JobRepository


@pytest.mark.asyncio
async def test_scheduler_is_idempotent_per_workflow_state(tmp_path):
    database = tmp_path / "application.sqlite3"
    repository = JobRepository(database)
    repository.initialize()
    queue = SQLiteJobQueue(str(database))
    repository.create_job("https://www.facebook.com/reel/123", job_id="job-1")
    scheduler = ScheduleWorkflowJobsUseCase(repository, queue)

    assert await scheduler.schedule_once() == 1
    assert await scheduler.schedule_once() == 0
    assert len(await queue.list_records()) == 1

    repository.transition("job-1", JobStatus.DOWNLOADREEL_RUNNING)
    repository.transition("job-1", JobStatus.DOWNLOADED)
    assert await scheduler.schedule_once() == 1
    records = await queue.list_records()
    assert {record["job_id"] for record in records} == {
        "job-1:CREATED",
        "job-1:DOWNLOADED",
    }


@pytest.mark.asyncio
async def test_publish_confirmation_only_accepts_manual_gate(tmp_path):
    database = tmp_path / "application.sqlite3"
    repository = JobRepository(database)
    repository.initialize()
    queue = SQLiteJobQueue(str(database))
    repository.create_job("https://www.facebook.com/reel/123", job_id="job-2")
    scheduler = ScheduleWorkflowJobsUseCase(repository, queue)

    with pytest.raises(ValueError):
        await scheduler.schedule_publish_confirmation("job-2")

    for status in (
        JobStatus.DOWNLOADREEL_RUNNING,
        JobStatus.DOWNLOADED,
        JobStatus.AI_ANALYZING,
        JobStatus.CLINICAL_FACTORS_GENERATED,
        JobStatus.CDHA_OPENING,
        JobStatus.CDHA_UPLOADING,
        JobStatus.CDHA_ANALYZING,
        JobStatus.CDHA_ANALYZED,
        JobStatus.SCREENSHOTS_CAPTURING,
        JobStatus.SCREENSHOTS_CAPTURED,
        JobStatus.WAITING_FOR_REVIEW,
        JobStatus.APPROVED,
        JobStatus.FACEBOOK_PREPARING,
        JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
    ):
        repository.transition("job-2", status)

    assert await scheduler.schedule_publish_confirmation("job-2") is True
    assert await scheduler.schedule_publish_confirmation("job-2") is False
