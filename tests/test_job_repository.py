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


def test_invalid_transition_does_not_modify_database(repository: JobRepository) -> None:
    repository.create_job("https://www.facebook.com/reel/123", job_id="job-3")

    with pytest.raises(InvalidTransitionError):
        repository.transition("job-3", WorkflowStatus.COMPLETED)

    assert repository.get_job("job-3").status is WorkflowStatus.CREATED
    assert len(repository.list_events("job-3")) == 1


def test_unknown_job_raises(repository: JobRepository) -> None:
    with pytest.raises(JobNotFoundError):
        repository.transition("missing", WorkflowStatus.DOWNLOADREEL_RUNNING)


def test_find_latest_by_source_url(repository: JobRepository) -> None:
    source = "https://www.facebook.com/reel/456"
    created = repository.create_job(source, job_id="job-4")
    assert repository.find_latest_by_source_url(source) == created
