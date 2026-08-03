from __future__ import annotations

from pathlib import Path

import pytest

from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobNotFoundError, JobRepository
from app.workflows.state_machine import InvalidTransitionError


@pytest.fixture
def repository(tmp_path: Path) -> JobRepository:
    instance = JobRepository(tmp_path / "jobs.sqlite3")
    instance.initialize()
    return instance


def test_create_job_persists_initial_event(repository: JobRepository) -> None:
    job = repository.create_job("https://www.facebook.com/reel/123", job_id="job-1")

    assert job.status is WorkflowStatus.CREATED
    assert repository.get_job("job-1") == job
    events = repository.list_events("job-1")
    assert len(events) == 1
    assert events[0].from_status is None
    assert events[0].to_status is WorkflowStatus.CREATED


def test_transition_is_atomic_and_patches_data(repository: JobRepository) -> None:
    repository.create_job("https://www.facebook.com/reel/123", job_id="job-2")
    updated = repository.transition(
        "job-2",
        WorkflowStatus.DOWNLOADREEL_RUNNING,
        details={"attempt": 1},
        data_patch={"working_directory": "/tmp/job-2"},
    )

    assert updated.status is WorkflowStatus.DOWNLOADREEL_RUNNING
    assert updated.data["working_directory"] == "/tmp/job-2"
    events = repository.list_events("job-2")
    assert [event.to_status for event in events] == [
        WorkflowStatus.CREATED,
        WorkflowStatus.DOWNLOADREEL_RUNNING,
    ]
    assert events[-1].details == {"attempt": 1}


def test_artifact_paths_are_explicit_and_deduplicated(repository: JobRepository) -> None:
    repository.create_job("https://www.facebook.com/reel/789", job_id="job-artifacts")

    first = repository.update_data(
        "job-artifacts",
        {"video_path": "/tmp/video.mp4", "source_url": "https://example.invalid"},
    )
    second = repository.update_data(
        "job-artifacts",
        {"screenshot_paths": ["/tmp/one.png", "/tmp/video.mp4"]},
    )

    assert first.artifact_paths == ["/tmp/video.mp4"]
    assert second.artifact_paths == ["/tmp/video.mp4", "/tmp/one.png"]


def test_invalid_transition_does_not_modify_database(repository: JobRepository) -> None:
    repository.create_job("https://www.facebook.com/reel/123", job_id="job-3")

    with pytest.raises(InvalidTransitionError):
        repository.transition("job-3", WorkflowStatus.COMPLETED)

    assert repository.get_job("job-3").status is WorkflowStatus.CREATED
    assert len(repository.list_events("job-3")) == 1


def test_transition_error_includes_job_states_and_reason(repository: JobRepository) -> None:
    repository.create_job("https://www.facebook.com/reel/error", job_id="job-error")

    with pytest.raises(InvalidTransitionError) as caught:
        repository.transition(
            "job-error",
            WorkflowStatus.COMPLETED,
            details={"reason": "publishing failed"},
        )

    message = str(caught.value)
    assert "job-error" in message
    assert "CREATED" in message
    assert "COMPLETED" in message
    assert "publishing failed" in message


def test_failed_publish_cannot_skip_retry_and_complete(repository: JobRepository) -> None:
    repository.create_job("https://www.facebook.com/reel/fail", job_id="job-fail")
    repository.transition("job-fail", WorkflowStatus.DOWNLOADREEL_RUNNING)
    repository.transition("job-fail", WorkflowStatus.DOWNLOADED)
    repository.transition("job-fail", WorkflowStatus.AI_ANALYZING)
    repository.transition("job-fail", WorkflowStatus.CLINICAL_FACTORS_GENERATED)
    repository.transition("job-fail", WorkflowStatus.CDHA_OPENING)
    repository.transition("job-fail", WorkflowStatus.CDHA_UPLOADING)
    repository.transition("job-fail", WorkflowStatus.CDHA_ANALYZING)
    repository.transition("job-fail", WorkflowStatus.CDHA_ANALYZED)
    repository.transition("job-fail", WorkflowStatus.SCREENSHOTS_CAPTURING)
    repository.transition("job-fail", WorkflowStatus.SCREENSHOTS_CAPTURED)
    repository.transition("job-fail", WorkflowStatus.WAITING_FOR_REVIEW)
    repository.transition("job-fail", WorkflowStatus.APPROVED)
    repository.transition("job-fail", WorkflowStatus.FACEBOOK_PREPARING)
    repository.transition("job-fail", WorkflowStatus.FACEBOOK_PUBLISH_FAILED)

    with pytest.raises(InvalidTransitionError):
        repository.transition("job-fail", WorkflowStatus.COMPLETED)

    assert repository.get_job("job-fail").status is WorkflowStatus.FACEBOOK_PUBLISH_FAILED


def test_transition_round_trips_output_and_artifact_paths(repository: JobRepository) -> None:
    repository.create_job("https://www.facebook.com/reel/artifact", job_id="job-roundtrip")
    repository.transition(
        "job-roundtrip",
        WorkflowStatus.DOWNLOADREEL_RUNNING,
        data_patch={"video_path": "/tmp/job-roundtrip.mp4", "caption": "verified"},
    )

    persisted = repository.get_job("job-roundtrip")
    assert persisted.output_payload == persisted.data
    assert persisted.artifact_paths == ["/tmp/job-roundtrip.mp4"]


def test_unknown_job_raises(repository: JobRepository) -> None:
    with pytest.raises(JobNotFoundError):
        repository.transition("missing", WorkflowStatus.DOWNLOADREEL_RUNNING)


def test_find_latest_by_source_url(repository: JobRepository) -> None:
    source = "https://www.facebook.com/reel/456"
    created = repository.create_job(source, job_id="job-4")
    assert repository.find_latest_by_source_url(source) == created
