import pytest
import os
import time
import json
import asyncio
from pathlib import Path
from app.infrastructure.browser.file_browser_lock import FileBrowserLock

@pytest.mark.asyncio
async def test_acquire_and_release(tmp_path: Path):
    lock_path = tmp_path / "facebook_browser.lock"
    lock = FileBrowserLock(str(lock_path))
    
    assert await lock.acquire(job_id="job_123")
    assert lock_path.exists()
    
    # Second acquire should fail
    lock2 = FileBrowserLock(str(lock_path))
    assert not await lock2.acquire(job_id="job_456")
    
    await lock.release()
    assert not lock_path.exists()
    
    # After release, acquire should succeed
    assert await lock2.acquire(job_id="job_456")

@pytest.mark.asyncio
async def test_stale_lock_removal(tmp_path: Path):
    lock_path = tmp_path / "facebook_browser.lock"
    
    # Create a stale lock with dead PID
    # Hopefully 9999999 does not exist
    metadata = {
        "pid": 9999999,
        "hostname": "test",
        "created_at": time.time(),
        "job_id": "job_old",
        "worker_id": "worker-1"
    }
    lock_path.write_text(json.dumps(metadata))
    
    lock = FileBrowserLock(str(lock_path))
    assert await lock.acquire(job_id="job_new")
    
    content = json.loads(lock_path.read_text())
    assert content["job_id"] == "job_new"

@pytest.mark.asyncio
async def test_context_manager(tmp_path: Path):
    lock_path = tmp_path / "facebook_browser.lock"
    lock = FileBrowserLock(str(lock_path))
    
    try:
        async with lock as l:
            assert await l.acquire("job_err")
            assert lock_path.exists()
            raise ValueError("Test error")
    except ValueError:
        pass
        
    assert not lock_path.exists()

@pytest.mark.asyncio
async def test_corrupted_json_lock(tmp_path: Path):
    lock_path = tmp_path / "facebook_browser.lock"
    lock_path.write_text("{bad json}")
    
    lock = FileBrowserLock(str(lock_path))
    assert await lock.acquire(job_id="job_fix")
    
    content = json.loads(lock_path.read_text())
    assert content["job_id"] == "job_fix"


def _metadata(lock_path: Path) -> dict:
    return json.loads(lock_path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_lock_metadata_is_complete_and_release_requires_owner_token(tmp_path: Path):
    lock_path = tmp_path / "facebook_browser.lock"
    lock = FileBrowserLock(str(lock_path), browser_profile="facebook", browser_port=9222, timeout_seconds=120, heartbeat_seconds=15)
    assert await lock.acquire(job_id="job-metadata")
    metadata = _metadata(lock_path)
    assert {"pid", "process_name", "process_create_time", "hostname", "browser_profile", "browser_port", "job_id", "created_at", "heartbeat_at", "lock_owner_token"} <= metadata.keys()
    assert await lock.release(owner_token="wrong-token") is False
    assert lock_path.exists()
    assert await lock.release(owner_token=metadata["lock_owner_token"]) is True
    assert not lock_path.exists()


@pytest.mark.asyncio
async def test_pid_reuse_is_stale_and_original_is_archived(tmp_path: Path):
    lock_path = tmp_path / "facebook_browser.lock"
    first = FileBrowserLock(str(lock_path))
    assert await first.acquire("old-job")
    metadata = _metadata(lock_path)
    metadata["process_create_time"] = float(metadata["process_create_time"]) - 10
    lock_path.write_text(json.dumps(metadata), encoding="utf-8")
    second = FileBrowserLock(str(lock_path))
    assert await second.acquire("new-job")
    assert _metadata(lock_path)["job_id"] == "new-job"
    assert list(tmp_path.glob("facebook_browser.lock.stale.*"))


@pytest.mark.asyncio
async def test_corrupt_lock_is_archived_for_audit(tmp_path: Path):
    lock_path = tmp_path / "facebook_browser.lock"
    lock_path.write_text("{partial", encoding="utf-8")
    lock = FileBrowserLock(str(lock_path))
    assert await lock.acquire("replacement")
    assert list(tmp_path.glob("facebook_browser.lock.stale.*"))


@pytest.mark.asyncio
async def test_fresh_remote_heartbeat_is_not_stale(tmp_path: Path):
    lock_path = tmp_path / "facebook_browser.lock"
    lock = FileBrowserLock(str(lock_path), timeout_seconds=120)
    assert await lock.acquire("remote-job")
    metadata = _metadata(lock_path)
    metadata["hostname"] = "another-host"
    lock_path.write_text(json.dumps(metadata), encoding="utf-8")
    contender = FileBrowserLock(str(lock_path), timeout_seconds=120)
    assert await contender.is_lock_stale() is False
    assert await contender.acquire("new-job") is False


@pytest.mark.asyncio
async def test_expired_remote_heartbeat_recovers_and_heartbeat_updates(tmp_path: Path):
    lock_path = tmp_path / "facebook_browser.lock"
    lock = FileBrowserLock(str(lock_path), timeout_seconds=1, heartbeat_seconds=0.01)
    assert await lock.acquire("old-job")
    before = _metadata(lock_path)["heartbeat_at"]
    await asyncio.sleep(0.02)
    assert await lock.update_lock_heartbeat()
    assert _metadata(lock_path)["heartbeat_at"] > before
    metadata = _metadata(lock_path)
    metadata["hostname"] = "another-host"
    metadata["heartbeat_at"] = "2000-01-01T00:00:00+00:00"
    lock_path.write_text(json.dumps(metadata), encoding="utf-8")
    contender = FileBrowserLock(str(lock_path), timeout_seconds=1)
    assert await contender.acquire("new-job")
    assert _metadata(lock_path)["job_id"] == "new-job"
