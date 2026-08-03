from __future__ import annotations

import asyncio

import pytest

from app.application.services.facebook_job_dispatcher import FacebookJobDispatcher
from app.domain.enums.facebook_job_type import FacebookJobType
from app.domain.models.facebook_job import FacebookJob
from app.domain.models.job_result import JobResult
from app.infrastructure.persistence.sqlite_job_queue import SQLiteJobQueue
from workers.facebook_browser_worker import FacebookBrowserWorker


class BusyLock:
    def __init__(self):
        self.calls = 0
        self.metadata = {
            "pid": 4321, "job_id": "owner-job", "heartbeat_at": "2026-07-27T00:00:00+00:00",
        }

    async def acquire(self, job_id=None):
        self.calls += 1
        return False

    def read_metadata(self):
        return self.metadata

    async def recover_stale_lock(self):
        return None

    async def release(self):
        raise AssertionError("busy lock must not be released by contender")


class NeverDispatcher:
    async def dispatch(self, job):
        raise AssertionError("job must not dispatch without browser lock")


class OwnedLock:
    def __init__(self):
        self.released = 0

    async def acquire(self, job_id=None):
        return True

    async def release(self):
        self.released += 1
        return True


class CrashedBrowserDispatcher:
    async def dispatch(self, job):
        raise TimeoutError("browser disconnected while page crashed")


class FailedResultDispatcher:
    async def dispatch(self, job):
        return JobResult.failure_result(job.job_id, "composer could not be opened")


@pytest.mark.asyncio
async def test_start_once_closes_resources_when_queue_is_empty(tmp_path):
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    lock = OwnedLock()
    closed = 0

    async def close_resources():
        nonlocal closed
        closed += 1

    worker = FacebookBrowserWorker(
        queue, lock, NeverDispatcher(), close_resources=close_resources
    )

    assert await worker.start_once() is False
    assert lock.released == 1
    assert closed == 1


@pytest.mark.asyncio
async def test_completed_job_is_not_requeued_by_duplicate_enqueue(tmp_path):
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    job = FacebookJob("job-1", FacebookJobType.CREATE_POST, {"content": "one"})
    assert await queue.enqueue(job) is True
    claimed = await queue.dequeue()
    assert claimed and claimed.job_id == job.job_id
    await queue.complete(job.job_id)
    duplicate = FacebookJob("job-1", FacebookJobType.CREATE_POST, {"content": "two"})
    assert await queue.enqueue(duplicate) is False
    assert (await queue.get_record(job.job_id))["status"] == "COMPLETED"
    assert await queue.dequeue() is None


@pytest.mark.asyncio
async def test_two_workers_cannot_claim_the_same_row(tmp_path):
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    job = FacebookJob("job-atomic", FacebookJobType.CREATE_POST, {"content": "one"})
    await queue.enqueue(job)

    claims = await asyncio.gather(
        queue.dequeue(worker_id="worker-a"),
        queue.dequeue(worker_id="worker-b"),
    )

    claimed = [item for item in claims if item is not None]
    assert [item.job_id for item in claimed] == [job.job_id]
    record = await queue.get_record(job.job_id)
    assert record["claimed_by"] in {"worker-a", "worker-b"}


@pytest.mark.asyncio
async def test_unsuccessful_dispatch_result_is_not_marked_completed(tmp_path):
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    job = FacebookJob("job-failed-result", FacebookJobType.CREATE_POST, {"content": "one"})
    await queue.enqueue(job)
    lock = OwnedLock()
    worker = FacebookBrowserWorker(queue, lock, FailedResultDispatcher())

    assert await worker.run_once() is True
    record = await queue.get_record(job.job_id)
    assert record["status"] == "FAILED"
    assert record["error_message"] == "composer could not be opened"
    assert lock.released == 1


@pytest.mark.asyncio
async def test_lock_wait_timeout_is_retryable_with_durable_event(tmp_path):
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    job = FacebookJob("job-lock", FacebookJobType.CREATE_POST, {}, max_attempts=3)
    await queue.enqueue(job)
    lock = BusyLock()
    worker = FacebookBrowserWorker(
        queue=queue, browser_lock=lock, dispatcher=NeverDispatcher(),
        lock_wait_timeout_seconds=0.025, lock_retry_interval_seconds=0.005,
        retry_jitter_seconds=0,
    )
    assert await worker.run_once() is True
    record = await queue.get_record(job.job_id)
    assert record["status"] == "RETRYABLE"
    assert record["attempt_count"] == 1
    assert record["next_retry_at"] > 0
    events = await queue.list_events(job.job_id)
    assert any(event["event_type"] == "BROWSER_LOCK_WAITING" for event in events)
    assert any(event["event_type"] == "PLAYWRIGHT_RETRY_SCHEDULED" for event in events)
    assert lock.calls >= 2


@pytest.mark.asyncio
async def test_interrupted_job_recovers_without_duplicate_row(tmp_path):
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    job = FacebookJob("job-crash", FacebookJobType.DOWNLOAD_REEL, {"url": "fixture"})
    await queue.enqueue(job)
    assert await queue.dequeue(worker_id="crashed-worker", lease_seconds=60)
    with queue._connect() as connection:
        connection.execute(
            "UPDATE queue SET lease_expires_at = 0 WHERE job_id = ?", (job.job_id,)
        )
    assert await queue.recover_jobs() == 1
    record = await queue.get_record(job.job_id)
    assert record["status"] == "RETRYABLE"
    assert record["attempt_count"] == 1
    assert len(await queue.list_records()) == 1


@pytest.mark.asyncio
async def test_recovery_does_not_steal_fresh_claim_and_heartbeat_requires_owner(tmp_path):
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    job = FacebookJob("job-live", FacebookJobType.DOWNLOAD_REEL, {"url": "fixture"})
    await queue.enqueue(job)
    assert await queue.dequeue(worker_id="worker-a", lease_seconds=60)

    assert await queue.heartbeat(
        job.job_id, worker_id="worker-b", lease_seconds=60
    ) is False
    assert await queue.heartbeat(
        job.job_id, worker_id="worker-a", lease_seconds=60
    ) is True
    assert await queue.recover_jobs() == 0
    record = await queue.get_record(job.job_id)
    assert record["status"] == "ACQUIRING_BROWSER_LOCK"
    assert record["claimed_by"] == "worker-a"


@pytest.mark.asyncio
async def test_browser_crash_retries_with_limit_and_releases_lock(tmp_path):
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    job = FacebookJob("job-crash-browser", FacebookJobType.CREATE_POST, {}, max_attempts=2)
    await queue.enqueue(job)
    lock = OwnedLock()
    worker = FacebookBrowserWorker(
        queue, lock, CrashedBrowserDispatcher(),
        lock_wait_timeout_seconds=0.01, retry_base_seconds=0,
        retry_max_seconds=0, retry_jitter_seconds=0,
    )

    assert await worker.run_once() is True
    record = await queue.get_record(job.job_id)
    assert record["status"] == "RETRYABLE"
    assert record["attempt_count"] == 1
    assert lock.released == 1
