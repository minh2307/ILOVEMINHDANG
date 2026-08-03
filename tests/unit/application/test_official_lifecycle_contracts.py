from __future__ import annotations

import json

import pytest

from app.application.use_cases.confirm_publish_use_case import ConfirmPublishUseCase
from app.application.use_cases.get_job_status_use_case import GetJobStatusUseCase
from app.application.use_cases.resume_job_use_case import ResumeJobUseCase
from app.application.use_cases.review_job_use_case import ReviewJobUseCase
from app.application.use_cases.retry_job_use_case import RetryJobUseCase
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.domain.enums.job_status import JobStatus
from app.infrastructure.persistence.sqlite_job_queue import SQLiteJobQueue
from app.infrastructure.persistence.sqlite_job_repository import JobRepository


@pytest.fixture
def lifecycle(tmp_path):
    database = tmp_path / "official-lifecycle.sqlite3"
    repository = JobRepository(database)
    repository.initialize()
    queue = SQLiteJobQueue(str(database))
    scheduler = ScheduleWorkflowJobsUseCase(repository, queue)
    return repository, queue, scheduler


def _fail_download(repository: JobRepository, job_id: str) -> None:
    repository.transition(job_id, JobStatus.DOWNLOADREEL_RUNNING)
    repository.transition(
        job_id,
        JobStatus.DOWNLOADREEL_FAILED,
        details={"error": "temporary network failure", "reason": "download failed"},
    )


@pytest.mark.asyncio
async def test_retry_persists_metadata_increments_once_and_is_idempotent(lifecycle):
    repository, queue, scheduler = lifecycle
    repository.create_job(
        "https://facebook.com/reel/retry-once",
        job_id="retry-once",
        max_attempts=2,
    )
    _fail_download(repository, "retry-once")
    use_case = RetryJobUseCase(repository, scheduler)

    first = await use_case.execute(
        "retry-once", reason="operator requested retry", requested_by="cli"
    )
    duplicate = await use_case.execute(
        "retry-once", reason="duplicate request", requested_by="cli"
    )

    assert first.success is True
    assert duplicate.success is True
    assert duplicate.data["duplicate_retry"] is True
    persisted = repository.get_job("retry-once")
    assert persisted is not None
    assert persisted.status is JobStatus.RETRY_PENDING
    assert persisted.attempt_count == 1
    assert persisted.data["previous_failure_state"] == "DOWNLOADREEL_FAILED"
    assert persisted.data["failure_stage"] == "download"
    assert persisted.data["retry_reason"] == "operator requested retry"
    assert persisted.data["retry_requested_by"] == "cli"
    assert persisted.data["retry_attempt"] == 1
    assert persisted.data["max_attempts"] == 2
    assert persisted.data["retry_requested_at"]
    assert persisted.data["next_retry_at"]
    records = await queue.list_records()
    assert [record["job_id"] for record in records] == [
        "retry-once:RETRY_PENDING:attempt-1"
    ]


@pytest.mark.asyncio
async def test_retry_enforces_maximum_attempts_and_each_attempt_has_unique_queue_item(
    lifecycle,
):
    repository, queue, scheduler = lifecycle
    repository.create_job(
        "https://facebook.com/reel/retry-limit",
        job_id="retry-limit",
        max_attempts=2,
    )
    use_case = RetryJobUseCase(repository, scheduler)

    _fail_download(repository, "retry-limit")
    assert (await use_case.execute("retry-limit")).success is True
    repository.transition("retry-limit", JobStatus.DOWNLOADREEL_RUNNING)
    repository.transition("retry-limit", JobStatus.DOWNLOADREEL_FAILED)
    assert (await use_case.execute("retry-limit")).success is True
    repository.transition("retry-limit", JobStatus.DOWNLOADREEL_RUNNING)
    repository.transition("retry-limit", JobStatus.DOWNLOADREEL_FAILED)

    exhausted = await use_case.execute("retry-limit")

    assert exhausted.success is False
    assert "maximum retry attempts" in (exhausted.error or "").lower()
    assert repository.get_job("retry-limit").attempt_count == 2
    assert [record["job_id"] for record in await queue.list_records()] == [
        "retry-limit:RETRY_PENDING:attempt-1",
        "retry-limit:RETRY_PENDING:attempt-2",
    ]


@pytest.mark.asyncio
async def test_resume_schedules_from_persisted_boundary_without_repeating_download(lifecycle):
    repository, queue, scheduler = lifecycle
    repository.create_job("https://facebook.com/reel/resume", job_id="resume")
    repository.transition("resume", JobStatus.DOWNLOADREEL_RUNNING)
    repository.transition(
        "resume",
        JobStatus.DOWNLOADED,
        data_patch={"video_path": "/tmp/already-downloaded.mp4"},
    )
    use_case = ResumeJobUseCase(repository, scheduler)

    first = await use_case.execute("resume")
    duplicate = await use_case.execute("resume")

    assert first.success is True and first.data["queued"] is True
    assert duplicate.success is True and duplicate.data["queued"] is False
    assert repository.get_job("resume").status is JobStatus.DOWNLOADED
    record = (await queue.list_records())[0]
    assert json.loads(record["payload"])["scheduled_from_status"] == "DOWNLOADED"


@pytest.mark.asyncio
async def test_resume_never_republishes_uncertain_facebook_result(lifecycle):
    repository, queue, scheduler = lifecycle
    repository.create_job("https://facebook.com/reel/uncertain", job_id="uncertain")
    current = "uncertain"
    path = [
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
        JobStatus.FACEBOOK_PUBLISHING,
        JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
    ]
    for status in path:
        repository.transition(current, status)

    result = await ResumeJobUseCase(repository, scheduler).execute(current)

    assert result.success is False
    assert "reconcil" in (result.error or "").lower()
    assert await queue.list_records() == []


@pytest.mark.asyncio
async def test_confirm_publish_validates_exact_phrase_and_is_idempotent(lifecycle):
    repository, queue, scheduler = lifecycle
    repository.create_job("https://facebook.com/reel/publish", job_id="publish")
    path = [
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
    ]
    for status in path:
        repository.transition("publish", status)
    use_case = ConfirmPublishUseCase(repository, scheduler)

    rejected = await use_case.execute("publish", confirmation="yes")
    first = await use_case.execute("publish", confirmation="PUBLISH publish")
    duplicate = await use_case.execute("publish", confirmation="PUBLISH publish")

    assert rejected.success is False
    assert first.success is True and first.data["queued"] is True
    assert duplicate.success is True and duplicate.data["queued"] is False
    assert len(await queue.list_records()) == 1


@pytest.mark.asyncio
async def test_status_use_case_returns_persisted_state_and_queue_history(lifecycle):
    repository, queue, scheduler = lifecycle
    repository.create_job("https://facebook.com/reel/status", job_id="status")
    await scheduler.schedule_job("status")

    result = await GetJobStatusUseCase(repository, queue).execute("status")

    assert result.success is True
    assert result.data["job"]["status"] == "CREATED"
    assert len(result.data["events"]) == 1
    assert len(result.data["queue_items"]) == 1


@pytest.mark.asyncio
async def test_retry_state_survives_repository_restart(lifecycle):
    repository, _queue, scheduler = lifecycle
    repository.create_job("https://facebook.com/reel/restart", job_id="restart")
    _fail_download(repository, "restart")
    result = await RetryJobUseCase(repository, scheduler).execute(
        "restart", reason="persist across restart", requested_by="test"
    )
    assert result.success is True

    reopened = JobRepository(repository.database_path)
    reopened.initialize()
    persisted = reopened.get_job("restart")
    assert persisted is not None
    assert persisted.status is JobStatus.RETRY_PENDING
    assert persisted.attempt_count == 1
    assert persisted.data["retry_reason"] == "persist across restart"
    assert persisted.data["retry_requested_by"] == "test"


@pytest.mark.asyncio
async def test_resume_after_verified_cdha_does_not_resubmit_cdha(lifecycle):
    repository, queue, scheduler = lifecycle
    repository.create_job("https://facebook.com/reel/cdha", job_id="cdha")
    for status in (
        JobStatus.DOWNLOADREEL_RUNNING,
        JobStatus.DOWNLOADED,
        JobStatus.AI_ANALYZING,
        JobStatus.CLINICAL_FACTORS_GENERATED,
        JobStatus.CDHA_OPENING,
        JobStatus.CDHA_UPLOADING,
        JobStatus.CDHA_ANALYZING,
        JobStatus.CDHA_ANALYZED,
    ):
        repository.transition("cdha", status)
    repository.update_data("cdha", {"cdha_external_analysis_id": "analysis-1"})

    result = await ResumeJobUseCase(repository, scheduler).execute("cdha")

    assert result.success is True
    assert repository.get_job("cdha").status is JobStatus.CDHA_ANALYZED
    record = (await queue.list_records())[0]
    assert json.loads(record["payload"])["scheduled_from_status"] == "CDHA_ANALYZED"


@pytest.mark.asyncio
async def test_resume_after_verified_publish_does_not_publish_again(lifecycle):
    repository, queue, scheduler = lifecycle
    repository.create_job("https://facebook.com/reel/published", job_id="published")
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
        JobStatus.FACEBOOK_PUBLISHING,
        JobStatus.FACEBOOK_PUBLISHED,
    ):
        repository.transition("published", status)
    repository.update_data(
        "published",
        {
            "facebook_publication_verified": True,
            "facebook_post_id": "post-1",
            "facebook_post_url_candidate": "https://facebook.com/posts/post-1",
        },
    )

    result = await ResumeJobUseCase(repository, scheduler).execute("published")

    assert result.success is True
    assert repository.get_job("published").status is JobStatus.FACEBOOK_PUBLISHED
    record = (await queue.list_records())[0]
    assert json.loads(record["payload"])["scheduled_from_status"] == "FACEBOOK_PUBLISHED"


@pytest.mark.asyncio
async def test_review_use_case_owns_review_to_queue_boundary(lifecycle):
    repository, queue, scheduler = lifecycle
    repository.create_job("https://facebook.com/reel/review", job_id="review")
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
    ):
        repository.transition("review", status)

    class Decision:
        action = "approved"
        exit_code = 0

    def reviewer(job_id: str):
        repository.transition(
            job_id,
            JobStatus.APPROVED,
            details={"review_decision": "approved"},
        )
        return Decision()

    result = await ReviewJobUseCase(repository, scheduler, reviewer).execute("review")

    assert result.success is True
    assert result.data == {
        "workflow_status": "APPROVED",
        "decision": "approved",
        "exit_code": 0,
        "queued": True,
    }
    assert repository.get_job("review").status is JobStatus.APPROVED
    assert len(await queue.list_records()) == 1
