from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import signal
import time
import uuid
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable
from typing import Any

from app.application.ports.browser_lock_port import BrowserLockPort
from app.application.ports.job_queue_port import JobQueuePort
from app.application.services.facebook_job_dispatcher import FacebookJobDispatcher
from app.domain.enums.job_status import JobStatus
from app.errors import PipelineError, QueueLeaseExpiredError


logger = logging.getLogger("cdha_pipeline.facebook_worker")


class FacebookBrowserWorker:
    """Durable worker with bounded lock waiting and classified retry behavior."""

    def __init__(
        self,
        queue: JobQueuePort,
        browser_lock: BrowserLockPort,
        dispatcher: FacebookJobDispatcher,
        *,
        lock_wait_timeout_seconds: float = 180,
        lock_retry_interval_seconds: float = 5,
        retry_base_seconds: float = 5,
        retry_multiplier: float = 2,
        retry_max_seconds: float = 40,
        retry_jitter_seconds: float = 1,
        worker_id: str | None = None,
        queue_lease_seconds: float = 120,
        queue_heartbeat_seconds: float = 30,
        poll_interval_seconds: float = 1,
        stage_timeout_seconds: float = 1200,
        close_resources: Callable[[], Awaitable[None]] | None = None,
        stage_provider: Callable[[Any], str] | None = None,
        startup_diagnostics: dict[str, Any] | None = None,
    ) -> None:
        self._queue = queue
        self._browser_lock = browser_lock
        self._dispatcher = dispatcher
        self._lock_wait_timeout = max(0.0, float(lock_wait_timeout_seconds))
        self._lock_retry_interval = max(0.001, float(lock_retry_interval_seconds))
        self._retry_base = max(0.0, float(retry_base_seconds))
        self._retry_multiplier = max(1.0, float(retry_multiplier))
        self._retry_max = max(self._retry_base, float(retry_max_seconds))
        self._retry_jitter = max(0.0, float(retry_jitter_seconds))
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex}"
        self._queue_lease_seconds = max(0.05, float(queue_lease_seconds))
        self._queue_heartbeat_seconds = min(
            max(0.01, float(queue_heartbeat_seconds)), self._queue_lease_seconds / 2
        )
        self._poll_interval_seconds = max(0.05, float(poll_interval_seconds))
        self._stage_timeout_seconds = max(0.001, float(stage_timeout_seconds))
        self._close_resources = close_resources
        self._stage_provider = stage_provider
        self._startup_diagnostics = dict(startup_diagnostics or {})
        self._startup_diagnostics_logged = False
        self._running = False
        self._setup_signals()

    
    @property
    def startup_diagnostics(self) -> dict[str, Any]:
        return dict(self._startup_diagnostics)

    
    @staticmethod
    def _heartbeat_age(metadata: dict[str, Any]) -> float | None:
        try:
            value = str(metadata.get("heartbeat_at", "")).replace("Z", "+00:00")
            heartbeat = datetime.fromisoformat(value)
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=UTC)
            return max(0.0, (datetime.now(UTC) - heartbeat).total_seconds())
        except (TypeError, ValueError):
            return None

    def _setup_signals(self) -> None:
        def handle_stop(*_: object) -> None:
            logger.info(
                "Worker shutdown requested",
                extra={"component": "worker", "event": "WORKER_SHUTDOWN_REQUESTED"},
            )
            self.stop()

        try:
            signal.signal(signal.SIGINT, handle_stop)
            signal.signal(signal.SIGTERM, handle_stop)
        except ValueError:
            # Unit tests or embedders may create a worker outside the main thread.
            logger.debug("Signal handlers are only available in the main thread")

    async def _record_event(
        self, job_id: str, event_type: str, details: dict[str, Any] | None = None
    ) -> None:
        await self._queue.record_event(job_id, event_type, details=details or {})

    async def _set_state(
        self, job_id: str, state: JobStatus, *, event_type: str = "JOB_STATE_CHANGED",
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._queue.set_state(
            job_id, state, event_type=event_type, details=details or {}
        )

    async def _recover_at_startup(self) -> None:
        if self._startup_diagnostics and not self._startup_diagnostics_logged:
            logger.info(
                "Official runtime configuration",
                extra={
                    "component": "worker",
                    "event": "STARTUP_CONFIGURATION",
                    "details": self._startup_diagnostics,
                },
            )
            self._startup_diagnostics_logged = True
        recover_lock = getattr(self._browser_lock, "recover_stale_lock", None)
        if recover_lock is not None:
            recovered_path = await recover_lock()
            if recovered_path:
                logger.warning(
                    "Recovered stale browser lock during worker startup",
                    extra={
                        "component": "browser_lock",
                        "event": "STALE_BROWSER_LOCK_RECOVERED",
                        "details": {"audit_path": str(recovered_path)},
                    },
                )
        count = await self._queue.recover_jobs()
        if count:
            logger.warning(
                "Recovered interrupted jobs",
                extra={
                    "component": "worker", "event": "JOB_RECOVERED",
                    "details": {"count": count},
                },
            )

    async def _wait_for_lock(self, job_id: str) -> bool:
        started = time.monotonic()
        retry = 0
        await self._set_state(job_id, JobStatus.WAITING_FOR_BROWSER_LOCK)
        while True:
            if await self._browser_lock.acquire(job_id=job_id):
                await self._set_state(
                    job_id, JobStatus.RUNNING, event_type="BROWSER_LOCK_ACQUIRED"
                )
                return True
            retry += 1
            waited = time.monotonic() - started
            reader = getattr(self._browser_lock, "read_metadata", None)
            owner = reader() if reader is not None else None
            owner = owner if isinstance(owner, dict) else {}
            details = {
                "owner_pid": owner.get("pid"),
                "owner_job_id": owner.get("job_id"),
                "heartbeat_age_seconds": self._heartbeat_age(owner),
                "waited_seconds": round(waited, 3),
                "retry": retry,
            }
            logger.info(
                "Browser lock is currently held",
                extra={
                    "component": "browser_lock", "event": "BROWSER_LOCK_WAITING",
                    "job_id": job_id, "attempt": retry, "details": details,
                },
            )
            await self._record_event(job_id, "BROWSER_LOCK_WAITING", details)
            if waited >= self._lock_wait_timeout:
                return False
            await asyncio.sleep(min(self._lock_retry_interval, self._lock_wait_timeout - waited))
        return False

    def _retry_delay(self, attempt_count: int) -> float:
        base = min(
            self._retry_max,
            self._retry_base
            * (self._retry_multiplier ** max(0, attempt_count)),
        )
        return base + (random.uniform(0, self._retry_jitter) if self._retry_jitter else 0)

    async def _schedule_retry(self, job: Any, reason: str) -> None:
        await self._queue.retry(job.job_id, reason, self._retry_delay(job.attempt_count))

    async def _maintain_queue_lease(
        self, job: Any, lease_lost: asyncio.Event
    ) -> None:
        while True:
            await asyncio.sleep(self._queue_heartbeat_seconds)
            current_stage = JobStatus.RUNNING.value
            if self._stage_provider is not None:
                try:
                    current_stage = str(self._stage_provider(job) or current_stage)
                except Exception:
                    logger.warning(
                        "Unable to resolve current workflow stage for heartbeat",
                        extra={
                            "component": "worker",
                            "event": "WORKFLOW_STAGE_UNAVAILABLE",
                            "job_id": job.job_id,
                        },
                    )
            renewed = await self._queue.heartbeat(
                job.job_id,
                worker_id=self._worker_id,
                lease_seconds=self._queue_lease_seconds,
                current_stage=current_stage,
            )
            if not renewed:
                logger.error(
                    "Queue claim heartbeat was rejected",
                    extra={
                        "component": "worker",
                        "event": "QUEUE_LEASE_LOST",
                        "job_id": job.job_id,
                        "details": {"worker_id": self._worker_id},
                    },
                )
                lease_lost.set()
                return

    @staticmethod
    def _is_retryable_exception(exc: Exception) -> bool:
        if isinstance(exc, PipelineError):
            return bool(exc.retryable and not exc.manual_action_required)
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        markers = (
            "timeout", "targetclosed", "target closed", "page crash", "browser disconnect",
            "connection closed", "network", "temporar", "facebooktransienterror",
        )
        return any(marker in name or marker in message for marker in markers)

    async def _dispatch_with_lease_guard(
        self, job: Any, lease_lost: asyncio.Event
    ) -> Any:
        dispatch = asyncio.create_task(self._dispatcher.dispatch(job))
        lost = asyncio.create_task(lease_lost.wait())
        try:
            done, _pending = await asyncio.wait(
                {dispatch, lost},
                timeout=self._stage_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                dispatch.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await dispatch
                raise TimeoutError(
                    "Worker stage timeout exceeded "
                    f"({self._stage_timeout_seconds:g}s) at {JobStatus.RUNNING.value}"
                )
            if lost in done and lease_lost.is_set():
                dispatch.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await dispatch
                raise QueueLeaseExpiredError(
                    "Queue lease was lost while the workflow stage was running",
                    job_id=job.job_id,
                    phase=JobStatus.RUNNING.value,
                    operation="queue_heartbeat",
                    details={
                        "timeout_seconds": self._queue_lease_seconds,
                        "worker_id": self._worker_id,
                    },
                )
            return await dispatch
        finally:
            lost.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await lost

    async def run_once(self) -> bool:
        job = await self._queue.dequeue(
            worker_id=self._worker_id, lease_seconds=self._queue_lease_seconds
        )
        if job is None:
            return False

        lease_lost = asyncio.Event()
        queue_heartbeat = asyncio.create_task(
            self._maintain_queue_lease(job, lease_lost)
        )
        try:
            acquired = await self._wait_for_lock(job.job_id)
            if not acquired:
                await self._schedule_retry(job, "Browser lock wait timeout")
                return True

            start_heartbeat = getattr(self._browser_lock, "start_heartbeat", None)
            stop_heartbeat = getattr(self._browser_lock, "stop_heartbeat", None)
            if start_heartbeat is not None:
                start_heartbeat()
            try:
                result = await self._dispatch_with_lease_guard(job, lease_lost)
                if result.success:
                    await self._queue.complete(job.job_id)
                else:
                    await self._queue.fail(
                        job.job_id, result.error or "Facebook job returned an unsuccessful result"
                    )
            except (KeyboardInterrupt, SystemExit):
                logger.warning(
                    "Worker interrupted during job execution",
                    extra={"component": "worker", "event": "WORKER_SHUTDOWN", "job_id": job.job_id},
                )
                raise
            except Exception as exc:
                logger.exception(
                    "Facebook job dispatch failed",
                    extra={
                        "component": "worker", "event": "JOB_FAILED", "job_id": job.job_id,
                        "attempt": job.attempt_count, "details": {"error_type": type(exc).__name__},
                    },
                )
                if self._is_retryable_exception(exc):
                    await self._schedule_retry(job, str(exc))
                else:
                    await self._queue.fail(job.job_id, str(exc))
            finally:
                if stop_heartbeat is not None:
                    await stop_heartbeat()
                released = await self._browser_lock.release()
                await self._record_event(
                    job.job_id, "BROWSER_LOCK_RELEASED", {"released": bool(released)}
                )
        finally:
            queue_heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await queue_heartbeat
        return True

    async def start(self) -> None:
        self._running = True
        await self._recover_at_startup()
        try:
            while self._running:
                processed = await self.run_once()
                if not processed:
                    await asyncio.sleep(self._poll_interval_seconds)
        finally:
            stop_heartbeat = getattr(self._browser_lock, "stop_heartbeat", None)
            if stop_heartbeat is not None:
                await stop_heartbeat()
            await self._browser_lock.release()
            if self._close_resources is not None:
                await self._close_resources()

    async def start_once(self) -> bool:
        """Recover stale resources, then claim and process at most one work item."""
        try:
            await self._recover_at_startup()
            return await self.run_once()
        finally:
            await self._browser_lock.release()
            if self._close_resources is not None:
                await self._close_resources()

    def stop(self) -> None:
        self._running = False
