from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import socket
import tempfile
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from app.application.ports.browser_lock_port import BrowserLockPort

logger = logging.getLogger("cdha_pipeline.browser_lock")

_REQUIRED_FIELDS = frozenset({
    "pid", "process_name", "process_create_time", "hostname",
    "browser_profile", "browser_port", "job_id", "created_at",
    "heartbeat_at", "lock_owner_token", "process_cmdline_hash",
})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class BrowserLockUnavailable(RuntimeError):
    pass


class FileBrowserLock(BrowserLockPort):
    """Cross-process browser lock with atomic metadata and stale-owner recovery."""

    def __init__(
        self,
        lock_path: str,
        *,
        process_name: str = "cdha-worker",
        browser_profile: str = "facebook",
        browser_port: int = 9222,
        timeout_seconds: float = 120,
        heartbeat_seconds: float = 15,
    ) -> None:
        self.lock_path = Path(lock_path)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.process_name = process_name
        self.browser_profile = browser_profile
        self.browser_port = int(browser_port)
        self.timeout_seconds = float(timeout_seconds)
        self.heartbeat_seconds = float(heartbeat_seconds)
        self.hostname = socket.gethostname()
        self.owner_token: str | None = None
        self._current_job_id: str | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    @property
    def guard_path(self) -> Path:
        return self.lock_path.with_suffix(self.lock_path.suffix + ".guard")

    @contextmanager
    def _guard(self) -> Iterator[None]:
        descriptor = os.open(self.guard_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @staticmethod
    def _process_create_time(pid: int) -> float | None:
        """Linux /proc start time, comparable across processes and resistant to PID reuse."""
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            fields = stat_text[stat_text.rfind(")") + 2 :].split()
            start_ticks = int(fields[19])
            clock_ticks = int(os.sysconf("SC_CLK_TCK"))
            boot_line = next(
                line for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines()
                if line.startswith("btime ")
            )
            boot_time = int(boot_line.split()[1])
            return boot_time + (start_ticks / clock_ticks)
        except (FileNotFoundError, OSError, ValueError, IndexError, StopIteration):
            return None

    @staticmethod
    def _process_cmdline_hash(pid: int) -> str | None:
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
            if not command:
                return None
            return hashlib.sha256(command).hexdigest()
        except (FileNotFoundError, PermissionError, OSError):
            return None

    def _owner_metadata(self, job_id: str, token: str) -> dict[str, Any]:
        pid = os.getpid()
        create_time = self._process_create_time(pid)
        cmdline_hash = self._process_cmdline_hash(pid)
        if create_time is None or cmdline_hash is None:
            raise RuntimeError("Unable to establish current process identity for browser lock")
        now = _utc_now()
        return {
            "pid": pid,
            "process_name": self.process_name,
            "process_create_time": create_time,
            "process_cmdline_hash": cmdline_hash,
            "hostname": self.hostname,
            "browser_profile": self.browser_profile,
            "browser_port": self.browser_port,
            "job_id": job_id,
            "created_at": now,
            "heartbeat_at": now,
            "lock_owner_token": token,
        }

    def _read_metadata_unlocked(self) -> dict[str, Any]:
        payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not _REQUIRED_FIELDS.issubset(payload):
            missing = sorted(_REQUIRED_FIELDS - set(payload if isinstance(payload, dict) else {}))
            raise ValueError(f"Browser lock metadata is incomplete: {missing}")
        return payload

    def read_metadata(self) -> dict[str, Any] | None:
        try:
            with self._guard():
                return self._read_metadata_unlocked() if self.lock_path.exists() else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _atomic_write_unlocked(self, payload: dict[str, Any]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.lock_path.name}.", suffix=".tmp", dir=self.lock_path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.lock_path)
            directory_fd = os.open(self.lock_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def is_lock_owner_alive(self, metadata: dict[str, Any] | None = None) -> bool:
        owner = metadata
        if owner is None:
            try:
                owner = self._read_metadata_unlocked()
            except (OSError, ValueError, json.JSONDecodeError):
                return False
        if owner.get("hostname") != self.hostname:
            return False
        try:
            pid = int(owner["pid"])
            expected_create_time = float(owner["process_create_time"])
        except (KeyError, TypeError, ValueError):
            return False
        if not self._pid_exists(pid):
            return False
        actual_create_time = self._process_create_time(pid)
        if actual_create_time is None or abs(actual_create_time - expected_create_time) > 0.01:
            return False
        actual_hash = self._process_cmdline_hash(pid)
        return bool(actual_hash and actual_hash == owner.get("process_cmdline_hash"))

    def _stale_reason_unlocked(self) -> str | None:
        if not self.lock_path.exists():
            return None
        try:
            metadata = self._read_metadata_unlocked()
            heartbeat_age = (datetime.now(UTC) - _parse_timestamp(metadata["heartbeat_at"])).total_seconds()
        except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
            return f"invalid_metadata:{type(exc).__name__}"
        if metadata.get("hostname") != self.hostname:
            return "remote_heartbeat_expired" if heartbeat_age > self.timeout_seconds else None
        if not self.is_lock_owner_alive(metadata):
            return "owner_process_dead_or_identity_mismatch"
        # A verified live process always wins over age/port heuristics. This prevents
        # deleting a healthy owner's lock during a slow browser startup.
        return None

    async def is_lock_stale(self) -> bool:
        with self._guard():
            return self._stale_reason_unlocked() is not None

    def _recover_stale_unlocked(self, reason: str) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        audit_path = self.lock_path.with_name(f"{self.lock_path.name}.stale.{stamp}")
        os.replace(self.lock_path, audit_path)
        audit_path.chmod(0o600)
        logger.warning(
            "Recovered stale browser lock",
            extra={
                "component": "browser_lock", "event": "STALE_BROWSER_LOCK_RECOVERED",
                "lock_path": str(self.lock_path), "stale_path": str(audit_path),
                "details": {"reason": reason},
            },
        )
        return audit_path

    async def recover_stale_lock(self) -> Path | None:
        with self._guard():
            reason = self._stale_reason_unlocked()
            if reason is None:
                return None
            return self._recover_stale_unlocked(reason)

    async def acquire(self, job_id: str | None = None) -> bool:
        return await self.acquire_browser_lock(job_id=job_id)

    async def acquire_browser_lock(self, job_id: str | None = None) -> bool:
        identifier = job_id or "unknown"
        token = str(uuid.uuid4())
        with self._guard():
            if self.lock_path.exists():
                reason = self._stale_reason_unlocked()
                if reason is None:
                    return False
                self._recover_stale_unlocked(reason)
            self._atomic_write_unlocked(self._owner_metadata(identifier, token))
        self.owner_token = token
        self._current_job_id = identifier
        logger.info(
            "Browser lock acquired",
            extra={
                "component": "browser_lock", "event": "BROWSER_LOCK_ACQUIRED",
                "job_id": identifier, "details": {"lock_path": str(self.lock_path)},
            },
        )
        return True

    async def release(self, owner_token: str | None = None) -> bool:
        return await self.release_browser_lock(owner_token=owner_token)

    async def release_browser_lock(self, owner_token: str | None = None) -> bool:
        token = owner_token or self.owner_token
        if not token:
            return False
        with self._guard():
            if not self.lock_path.exists():
                return False
            try:
                metadata = self._read_metadata_unlocked()
            except (OSError, ValueError, json.JSONDecodeError):
                return False
            if metadata.get("lock_owner_token") != token:
                logger.warning(
                    "Refusing to release browser lock owned by another process",
                    extra={"component": "browser_lock", "event": "BROWSER_LOCK_RELEASE_REFUSED"},
                )
                return False
            self.lock_path.unlink()
        self.owner_token = None
        logger.info(
            "Browser lock released",
            extra={
                "component": "browser_lock", "event": "BROWSER_LOCK_RELEASED",
                "job_id": self._current_job_id,
            },
        )
        return True

    async def update_lock_heartbeat(self) -> bool:
        token = self.owner_token
        if not token:
            return False
        with self._guard():
            if not self.lock_path.exists():
                return False
            try:
                metadata = self._read_metadata_unlocked()
            except (OSError, ValueError, json.JSONDecodeError):
                return False
            if metadata.get("lock_owner_token") != token:
                return False
            metadata["heartbeat_at"] = _utc_now()
            self._atomic_write_unlocked(metadata)
        return True

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_seconds)
                if not await self.update_lock_heartbeat():
                    logger.warning(
                        "Browser lock heartbeat lost ownership",
                        extra={"component": "browser_lock", "event": "BROWSER_LOCK_HEARTBEAT_FAILED"},
                    )
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Browser lock heartbeat failed",
                extra={"component": "browser_lock", "event": "BROWSER_LOCK_HEARTBEAT_FAILED"},
            )

    def start_heartbeat(self) -> None:
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        task, self._heartbeat_task = self._heartbeat_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @asynccontextmanager
    async def hold(self, job_id: str) -> AsyncIterator["FileBrowserLock"]:
        if not await self.acquire_browser_lock(job_id):
            raise BrowserLockUnavailable(f"Browser lock is held: {self.lock_path}")
        self.start_heartbeat()
        try:
            yield self
        finally:
            await self.stop_heartbeat()
            await self.release_browser_lock()

    async def __aenter__(self) -> "FileBrowserLock":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.stop_heartbeat()
        await self.release_browser_lock()
