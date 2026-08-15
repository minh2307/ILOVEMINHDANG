from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from app.application.dto.stage_execution_result import StageExecutionResult
from app.application.use_cases.process_job_use_case import ProcessJobUseCase
from app.application.use_cases.reconcile_publish_use_case import ReconcilePublishUseCase
from app.application.use_cases.retry_job_use_case import RetryJobUseCase
from app.domain.enums.facebook_publication_state import FacebookPublicationState
from app.domain.enums.job_status import JobStatus
from app.domain.exceptions.errors import InvalidTransitionError
from app.infrastructure.persistence.sqlite_job_repository import JobRepository
from app.models.results import FacebookPublishResult


JOB_ID = "affected-facebook-job"
FINGERPRINT = "a" * 64
TARGET = "https://www.facebook.com/me"
PERMALINK = "https://www.facebook.com/61589210652274/posts/122116710381307021"


def make_repo(tmp_path: Path) -> JobRepository:
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    repository.initialize()
    repository.create_job(
        "https://www.facebook.com/reel/1025826473695327",
        job_id=JOB_ID,
        data={
            "facebook_content_hash": FINGERPRINT,
            "facebook_target_url": TARGET,
        },
    )
    return repository


def force_status(
    repository: JobRepository,
    status: JobStatus,
    *,
    data: dict | None = None,
    max_attempts: int | None = None,
) -> None:
    assignments = ["status = ?"]
    params: list[object] = [status.value]
    if data is not None:
        assignments.extend(["data_json = ?", "output_payload_json = ?"])
        encoded = json.dumps(data)
        params.extend([encoded, encoded])
    if max_attempts is not None:
        assignments.append("max_attempts = ?")
        params.append(max_attempts)
    params.append(JOB_ID)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            f"UPDATE jobs SET {', '.join(assignments)} WHERE job_id = ?",
            tuple(params),
        )


def record_submit(
    repository: JobRepository,
    *,
    submitted_at: str = "2026-08-10T15:14:26+00:00",
) -> None:
    force_status(repository, JobStatus.FACEBOOK_PUBLISHING)
    repository.mark_facebook_submitting(
        JOB_ID,
        submitted_at=submitted_at,
        content_fingerprint=FINGERPRINT,
        target_url=TARGET,
    )
    repository.record_event(
        JOB_ID,
        event_type="publish_button_clicked",
        details={"submitted_at": submitted_at},
    )


class StageSpy:
    def __init__(self, repository: JobRepository) -> None:
        self.repository = repository
        self.facebook_calls = 0
        self.reconciliation_calls = 0

    async def facebook(self, job_id: str) -> StageExecutionResult:
        self.facebook_calls += 1
        return StageExecutionResult(True)

    async def reconcile_facebook(self, job_id: str) -> StageExecutionResult:
        self.reconciliation_calls += 1
        return StageExecutionResult(True)

    def __getattr__(self, _name: str):
        async def noop(_job_id: str) -> StageExecutionResult:
            return StageExecutionResult(True)

        return noop


class QueueSpy:
    def __init__(self) -> None:
        self.items = []

    async def enqueue(self, item) -> bool:
        self.items.append(item)
        return True


class SchedulerSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def schedule_job(self, _job_id: str) -> bool:
        self.calls += 1
        return True


class PublisherStub:
    def __init__(self, result: FacebookPublishResult) -> None:
        self.result = result
        self.calls = 0

    async def reconcile_publication(self, *, job_id: str) -> FacebookPublishResult:
        self.calls += 1
        return self.result


def test_clicked_event_blocks_publish_after_status_is_tampered_to_pending(
    tmp_path: Path,
) -> None:
    repository = make_repo(tmp_path)
    record_submit(repository)
    force_status(
        repository,
        JobStatus.PENDING,
        data={"retry_step": "facebook_prepare"},
    )
    stages = StageSpy(repository)

    asyncio.run(ProcessJobUseCase(repository, stages).execute(JOB_ID))

    assert stages.facebook_calls == 0
    assert stages.reconciliation_calls == 1
    assert repository.get_job(JOB_ID).status is JobStatus.FACEBOOK_PUBLISH_UNCERTAIN


def test_submitted_unconfirmed_retry_calls_only_reconciliation(tmp_path: Path) -> None:
    repository = make_repo(tmp_path)
    record_submit(repository)
    force_status(
        repository,
        JobStatus.RETRY_PENDING,
        data={
            "retry_step": "facebook_prepare",
            "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
        },
    )
    stages = StageSpy(repository)

    asyncio.run(ProcessJobUseCase(repository, stages).execute(JOB_ID))

    assert stages.facebook_calls == 0
    assert stages.reconciliation_calls == 1


def test_increasing_max_attempts_does_not_increase_publish_attempts(tmp_path: Path) -> None:
    repository = make_repo(tmp_path)
    record_submit(repository)
    before = repository.get_job(JOB_ID).publish_attempts

    force_status(repository, JobStatus.FACEBOOK_PUBLISHING, max_attempts=99)

    persisted = repository.get_job(JOB_ID)
    assert persisted.max_attempts == 99
    assert persisted.publish_attempts == before == 1


def test_crash_after_click_restarts_in_reconciliation_without_republish(tmp_path: Path) -> None:
    repository = make_repo(tmp_path)
    force_status(repository, JobStatus.FACEBOOK_PUBLISHING)
    repository.mark_facebook_submitting(
        JOB_ID,
        submitted_at="2026-08-10T15:14:26+00:00",
        content_fingerprint=FINGERPRINT,
        target_url=TARGET,
    )
    # Simulate a crash after the external click but before the post-click DB write,
    # followed by direct status/data tampering.
    force_status(repository, JobStatus.RETRY_PENDING, data={"retry_step": "facebook_prepare"})
    stages = StageSpy(repository)

    asyncio.run(ProcessJobUseCase(repository, stages).execute(JOB_ID))

    assert stages.facebook_calls == 0
    assert stages.reconciliation_calls == 1


def prepare_reconciliation(repository: JobRepository) -> None:
    record_submit(repository)
    force_status(
        repository,
        JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
        data={
            "facebook_content_hash": FINGERPRINT,
            "facebook_target_url": TARGET,
            "facebook_submitted_at": "2026-08-10T15:14:26+00:00",
            "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
        },
    )


def test_one_reconciliation_match_confirms_and_persists_permalink(tmp_path: Path) -> None:
    repository = make_repo(tmp_path)
    prepare_reconciliation(repository)
    publisher = PublisherStub(
        FacebookPublishResult(
            True,
            "PUBLISHED_VERIFIED",
            target_url=TARGET,
            post_id="122116710381307021",
            permalink=PERMALINK,
            verification_method="caption+time+images",
            job_id=JOB_ID,
        )
    )

    result = asyncio.run(ReconcilePublishUseCase(repository, publisher).execute(JOB_ID))

    persisted = repository.get_job(JOB_ID)
    assert result.success is True
    assert persisted.status is JobStatus.FACEBOOK_PUBLISHED
    assert persisted.data["facebook_post_url"] == PERMALINK
    assert persisted.data["facebook_publication_state"] == "PUBLISHED_CONFIRMED"


def test_two_reconciliation_matches_enter_manual_review(tmp_path: Path) -> None:
    repository = make_repo(tmp_path)
    prepare_reconciliation(repository)
    publisher = PublisherStub(
        FacebookPublishResult(
            False,
            "POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW",
            target_url=TARGET,
            job_id=JOB_ID,
            diagnostics={"matching_permalinks": [PERMALINK, PERMALINK + "2"]},
            error="Multiple matching Facebook posts found",
        )
    )

    asyncio.run(ReconcilePublishUseCase(repository, publisher).execute(JOB_ID))

    assert (
        repository.get_job(JOB_ID).status
        is JobStatus.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW
    )


def test_no_reconciliation_match_stays_unconfirmed_and_never_publishes(
    tmp_path: Path,
) -> None:
    repository = make_repo(tmp_path)
    prepare_reconciliation(repository)
    publisher = PublisherStub(
        FacebookPublishResult(
            False,
            "PUBLICATION_UNCERTAIN",
            target_url=TARGET,
            job_id=JOB_ID,
            error="not found",
        )
    )

    result = asyncio.run(ReconcilePublishUseCase(repository, publisher).execute(JOB_ID))

    persisted = repository.get_job(JOB_ID)
    assert result.success is False
    assert persisted.status is JobStatus.FACEBOOK_PUBLISH_UNCERTAIN
    assert persisted.data["facebook_publication_state"] == "SUBMITTED_UNCONFIRMED"
    assert persisted.publish_attempts == 1


def test_existing_permalink_short_circuits_without_timeline_scan(tmp_path: Path) -> None:
    repository = make_repo(tmp_path)
    prepare_reconciliation(repository)
    repository.update_data(JOB_ID, {"facebook_post_url": PERMALINK})
    publisher = PublisherStub(
        FacebookPublishResult(False, "SHOULD_NOT_RUN", job_id=JOB_ID)
    )

    result = asyncio.run(ReconcilePublishUseCase(repository, publisher).execute(JOB_ID))

    assert result.success is True
    assert publisher.calls == 0
    assert repository.get_job(JOB_ID).data["facebook_post_url"] == PERMALINK


def test_genuine_failed_before_submit_can_retry_publish(tmp_path: Path) -> None:
    repository = make_repo(tmp_path)
    force_status(
        repository,
        JobStatus.FACEBOOK_PUBLISH_FAILED,
        data={"facebook_publication_state": "FAILED_BEFORE_SUBMIT"},
    )
    scheduler = SchedulerSpy()

    result = asyncio.run(RetryJobUseCase(repository, scheduler).execute(JOB_ID))

    assert result.success is True
    assert repository.get_job(JOB_ID).status is JobStatus.RETRY_PENDING
    assert repository.get_job(JOB_ID).data["retry_step"] == "facebook_prepare"


def test_initialize_migrates_legacy_sqlite_without_losing_job(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, source_url TEXT NOT NULL, "
            "status TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}', "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy", "https://facebook.com/reel/1", "CREATED", "{}", "old", "old"),
        )

    repository = JobRepository(database)
    repository.initialize()

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
    assert {"publish_attempts", "reconciliation_attempts"} <= columns
    assert repository.get_job("legacy").status is JobStatus.CREATED


def test_quarantine_changes_only_the_affected_job(tmp_path: Path) -> None:
    repository = make_repo(tmp_path)
    other = repository.create_job("https://facebook.com/reel/other", job_id="other-job")
    record_submit(repository)
    repository.record_event(
        JOB_ID,
        event_type="FACEBOOK_SUBMITTING",
        details={"timestamp": "2026-08-10T16:17:59+00:00"},
    )
    other_before = repository.get_job(other.job_id).to_dict()

    repository.quarantine_possible_duplicate(
        JOB_ID,
        expected_fingerprint=FINGERPRINT,
        reason="multiple durable submit events",
    )

    assert repository.get_job(other.job_id).to_dict() == other_before
    assert (
        repository.get_job(JOB_ID).status
        is JobStatus.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW
    )


def test_transition_guard_uses_history_after_database_status_and_flags_are_edited(
    tmp_path: Path,
) -> None:
    repository = make_repo(tmp_path)
    record_submit(repository)
    force_status(repository, JobStatus.RETRY_PENDING, data={})

    with pytest.raises(InvalidTransitionError, match="durable Facebook submit evidence"):
        repository.transition(JOB_ID, JobStatus.FACEBOOK_PREPARING)


def test_publication_state_machine_forbids_reverse_after_submit() -> None:
    from app.domain.rules.facebook_publication_state_machine import (
        FacebookPublicationStateMachine,
    )

    forbidden = FacebookPublicationStateMachine.allowed_targets(
        FacebookPublicationState.SUBMITTED_UNCONFIRMED
    )
    assert FacebookPublicationState.FAILED_BEFORE_SUBMIT not in forbidden
    assert FacebookPublicationState.SUBMITTING not in forbidden
