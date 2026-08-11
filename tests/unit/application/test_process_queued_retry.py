from __future__ import annotations

import pytest

from app.application.dto.stage_execution_result import StageExecutionResult
from app.application.use_cases.process_job_use_case import ProcessJobUseCase
from app.application.use_cases.process_queued_job_use_case import ProcessQueuedJobUseCase
from app.application.use_cases.retry_job_use_case import RetryJobUseCase
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.domain.enums.job_status import JobStatus
from app.domain.enums.job_type import JobType
from app.domain.models.facebook_job import FacebookJob
from app.infrastructure.persistence.sqlite_job_queue import SQLiteJobQueue
from app.infrastructure.persistence.sqlite_job_repository import JobRepository


def advance_to_publish_gate(repository: JobRepository, job_id: str) -> None:
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
        repository.transition(job_id, status)


class PublishOutcomeStages:
    def __init__(self, repository: JobRepository, outcome: JobStatus) -> None:
        self.repository = repository
        self.outcome = outcome

    async def facebook(self, job_id: str) -> StageExecutionResult:
        self.repository.transition(job_id, JobStatus.FACEBOOK_PUBLISHING)
        publication_state = (
            "SUBMITTED_UNCONFIRMED"
            if self.outcome is JobStatus.FACEBOOK_PUBLISH_UNCERTAIN
            else "FAILED_BEFORE_SUBMIT"
        )
        self.repository.transition(
            job_id,
            self.outcome,
            details={"error": "simulated publication outcome"},
            data_patch={
                "facebook_publication_state": publication_state,
                "facebook_submission_status": publication_state,
            },
        )
        return StageExecutionResult(False, error="simulated publication outcome")

    async def download(self, job_id: str) -> StageExecutionResult:
        raise AssertionError("download must not repeat")

    async def analyze(self, job_id: str) -> StageExecutionResult:
        raise AssertionError("analysis must not repeat")

    async def analyze_cdha(self, job_id: str) -> StageExecutionResult:
        raise AssertionError("CDHA submission must not repeat")

    async def capture_screenshots(self, job_id: str) -> StageExecutionResult:
        raise AssertionError("screenshots must not repeat")

    async def reconcile_facebook(self, job_id: str) -> StageExecutionResult:
        raise AssertionError("explicit reconciliation is not expected in this run")

    async def extract_permalink(self, job_id: str) -> StageExecutionResult:
        raise AssertionError("permalink must not run")

    async def add_permalink_comment(self, job_id: str) -> StageExecutionResult:
        raise AssertionError("comment must not run")


def build_use_case(tmp_path, outcome: JobStatus):
    database = tmp_path / f"queued-{outcome.value}.sqlite3"
    repository = JobRepository(database)
    repository.initialize()
    repository.create_job("https://facebook.com/reel/publish", job_id="publish")
    advance_to_publish_gate(repository, "publish")
    queue = SQLiteJobQueue(str(database))
    scheduler = ScheduleWorkflowJobsUseCase(repository, queue)
    retry = RetryJobUseCase(repository, scheduler)
    process = ProcessJobUseCase(repository, PublishOutcomeStages(repository, outcome))
    queued = ProcessQueuedJobUseCase(process, repository, retry)
    work_item = FacebookJob(
        job_id="publish:CONFIRMED_FACEBOOK_PUBLISH",
        job_type=JobType.PROCESS_WORKFLOW,
        payload={
            "workflow_job_id": "publish",
            "confirm_facebook_publish": True,
        },
    )
    return repository, queue, queued, work_item


@pytest.mark.asyncio
async def test_definite_publish_failure_passes_through_retry_pending(tmp_path):
    repository, queue, use_case, work_item = build_use_case(
        tmp_path, JobStatus.FACEBOOK_PUBLISH_FAILED
    )

    result = await use_case.execute(work_item)

    assert result.success is True
    assert result.data["retry_scheduled"] is True
    persisted = repository.get_job("publish")
    assert persisted.data["facebook_publication_state"] == "FAILED_BEFORE_SUBMIT"
    assert persisted.status is JobStatus.RETRY_PENDING
    assert persisted.attempt_count == 1
    assert persisted.data["previous_failure_state"] == "FACEBOOK_PUBLISH_FAILED"
    assert [record["job_id"] for record in await queue.list_records()] == [
        "publish:RETRY_PENDING:attempt-1"
    ]


@pytest.mark.asyncio
async def test_uncertain_publish_retries_reconciliation_without_enqueueing_publish(tmp_path):
    repository, queue, use_case, work_item = build_use_case(
        tmp_path, JobStatus.FACEBOOK_PUBLISH_UNCERTAIN
    )

    with pytest.raises(RuntimeError, match="reconciliation"):
        await use_case.execute(work_item)

    assert repository.get_job("publish").status is JobStatus.FACEBOOK_PUBLISH_UNCERTAIN
    assert await queue.list_records() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("submission_status", ["SUBMITTING", "SUBMITTED_UNCONFIRMED"])
async def test_retry_use_case_blocks_failed_job_that_was_already_submitted(
    tmp_path, submission_status
):
    repository, queue, _use_case, _work_item = build_use_case(
        tmp_path, JobStatus.FACEBOOK_PUBLISH_FAILED
    )
    repository.transition("publish", JobStatus.FACEBOOK_PUBLISHING)
    repository.transition(
        "publish",
        JobStatus.FACEBOOK_PUBLISH_FAILED,
        data_patch={
            "facebook_submission_status": submission_status,
        },
    )
    retry = RetryJobUseCase(
        repository, ScheduleWorkflowJobsUseCase(repository, queue)
    )

    result = await retry.execute("publish", reason="operator retry")

    assert result.success is False
    assert "reconcil" in (result.error or "").lower()
    assert repository.get_job("publish").status is JobStatus.FACEBOOK_PUBLISH_FAILED
    assert await queue.list_records() == []
