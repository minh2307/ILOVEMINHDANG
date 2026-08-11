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
    scheduler = ScheduleWorkflowJobsUseCase(
        repository, queue, max_facebook_reconciliation_attempts=5
    )

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
    record = next(
        item
        for item in await queue.list_records()
        if item["job_id"] == "job-2:CONFIRMED_FACEBOOK_PUBLISH"
    )
    assert record["max_attempts"] == 6


@pytest.mark.asyncio
async def test_automatic_scheduler_recovers_both_configured_gate_states(tmp_path):
    database = tmp_path / "automatic-gates.sqlite3"
    repository = JobRepository(database)
    repository.initialize()
    queue = SQLiteJobQueue(str(database))
    repository.create_job("https://www.facebook.com/reel/review", job_id="review")
    repository.create_job("https://www.facebook.com/reel/publish", job_id="publish")

    before_review = (
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
    )
    for status in before_review:
        repository.transition("review", status)
        repository.transition("publish", status)
    for status in (
        JobStatus.APPROVED,
        JobStatus.FACEBOOK_PREPARING,
        JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
    ):
        repository.transition("publish", status)

    scheduler = ScheduleWorkflowJobsUseCase(
        repository,
        queue,
        auto_approve_review=True,
        require_facebook_confirmation=False,
    )

    assert await scheduler.schedule_once() == 2
    assert {record["job_id"] for record in await queue.list_records()} == {
        "review:WAITING_FOR_REVIEW",
        "publish:FACEBOOK_WAITING_FOR_MANUAL_REVIEW",
    }
