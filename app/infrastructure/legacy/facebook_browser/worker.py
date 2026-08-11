"""Legacy in-process browser worker retained for characterization tests only."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

from app.browser.facebook_browser_manager import FacebookBrowserManager
from app.browser.facebook_job import FacebookJob, FacebookJobStatus, FacebookJobStore, FacebookJobType, utc_now
from app.browser.facebook_page_state import FacebookPageState, FacebookStateDetector
from app.config.facebook_browser import FacebookBrowserConfig
from app.infrastructure.browser.file_browser_lock import FileBrowserLock
from app.infrastructure.facebook.reel_engagement_service import (
    FacebookReelEngagementService,
)


JobHandler = Callable[[FacebookJob, Any], Awaitable[Any]]


class FacebookBrowserWorker:
    """The sole serialized execution boundary for Facebook UI automation."""

    def __init__(
        self,
        manager: FacebookBrowserManager | None = None,
        store: FacebookJobStore | None = None,
        *,
        config: FacebookBrowserConfig | None = None,
        logger: logging.Logger | None = None,
        engagement_service: FacebookReelEngagementService | None = None,
    ):
        self.config = config or FacebookBrowserConfig.load()
        self.store = store or FacebookJobStore(self.config.queue_database_path)
        self.logger = logger or logging.getLogger("cdha_pipeline.facebook_worker")
        self._browser_lock = asyncio.Lock()
        manager_lock = getattr(manager, "browser_lock", None)
        self._file_lock = manager_lock or FileBrowserLock(
            str(self.config.lock_path),
            process_name="cdha-facebook-browser-worker",
            browser_profile=str(self.config.profile_path),
            browser_port=self.config.cdp_port,
            timeout_seconds=self.config.lock_timeout_seconds,
            heartbeat_seconds=self.config.lock_heartbeat_seconds,
        )
        self.manager = manager or FacebookBrowserManager(
            config=self.config, browser_lock=self._file_lock
        )
        self._state_detector = FacebookStateDetector()
        self._engagement_service = engagement_service or FacebookReelEngagementService(
            logger=self.logger
        )
        self._queue: asyncio.Queue[FacebookJob] = asyncio.Queue()
        self._handlers: dict[FacebookJobType, JobHandler] = {
            FacebookJobType.CHECK_LOGIN: self._check_login,
            FacebookJobType.ENGAGE_REEL: self._engage_reel,
        }
        self._stopping = False

    def register_handler(self, job_type: FacebookJobType, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    async def submit_job(self, job: FacebookJob) -> FacebookJob:
        persisted = await asyncio.to_thread(self.store.create, job)
        if persisted.job_id == job.job_id:
            await self._queue.put(persisted)
        return persisted

    async def like_reel_and_comments(
        self,
        reel_url: str,
        like_reel: bool = True,
        like_comments: bool = True,
        like_replies: bool = False,
    ) -> dict[str, Any]:
        """Run Reel engagement through this worker's serialized CDP session."""
        job = FacebookJob(
            FacebookJobType.ENGAGE_REEL,
            {
                "reel_url": reel_url,
                "like_reel": bool(like_reel),
                "like_comments": bool(like_comments),
                "like_replies": bool(like_replies),
            },
        )
        persisted = await asyncio.to_thread(self.store.create, job)
        completed = await self.execute(persisted)
        if completed.status is not FacebookJobStatus.SUCCESS:
            raise RuntimeError(
                completed.error_message or "Facebook Reel engagement failed"
            )
        return dict(completed.result or {})

    async def execute(self, job: FacebookJob) -> FacebookJob:
        started = time.monotonic()
        async with self._browser_lock:
            wait_started = time.monotonic()
            lock_retry = 0
            acquired = False
            while time.monotonic() - wait_started <= self.config.lock_wait_timeout_seconds:
                acquired = await self._file_lock.acquire(job_id=job.job_id)
                if acquired:
                    break
                lock_retry += 1
                owner = self._file_lock.read_metadata() or {}
                self.logger.info("Browser lock is currently held", extra={
                    "component": "browser_lock", "event": "BROWSER_LOCK_WAITING",
                    "job_id": job.job_id, "retry": lock_retry,
                    "waited_seconds": round(time.monotonic() - wait_started, 3),
                    "owner_pid": owner.get("pid"), "owner_job_id": owner.get("job_id"),
                    "heartbeat_at": owner.get("heartbeat_at"),
                })
                remaining = self.config.lock_wait_timeout_seconds - (time.monotonic() - wait_started)
                if remaining <= 0:
                    break
                await asyncio.sleep(min(self.config.lock_retry_interval_seconds, remaining))
            if not acquired:
                job.retry_count += 1
                job.status = (
                    FacebookJobStatus.RETRY_WAITING
                    if job.retry_count <= self.config.max_job_retries
                    else FacebookJobStatus.FAILED
                )
                if job.status is FacebookJobStatus.FAILED:
                    job.completed_at = utc_now()
                job.error_message = f"Browser lock unavailable after {self.config.lock_wait_timeout_seconds}s"
                await asyncio.to_thread(self.store.update, job)
                self._log(job, started)
                return job
            page = None
            self._file_lock.start_heartbeat()
            try:
                job.status = FacebookJobStatus.RUNNING
                job.started_at = utc_now()
                job.error_message = None
                await asyncio.to_thread(self.store.update, job)
                await self.manager.start()
                handler = self._handlers.get(job.job_type)
                if handler is None:
                    raise ValueError(f"No Facebook worker handler registered for {job.job_type.value}")
                page = await self.manager.tabs.get(self._tab_name(job.job_type), job_id=job.job_id, temporary=job.job_type is not FacebookJobType.CHECK_LOGIN)
                job.result = await handler(job, page)
                job.status = FacebookJobStatus.SUCCESS
                job.completed_at = utc_now()
            except Exception as exc:
                job.status = FacebookJobStatus.FAILED
                job.completed_at = utc_now()
                job.error_message = f"{type(exc).__name__}: {exc}"
                await self._diagnose(job, page, traceback.format_exc())
            finally:
                if self.manager.tabs is not None:
                    await self.manager.tabs.release_job(job.job_id)
                await asyncio.to_thread(self.store.update, job)
                await self._file_lock.stop_heartbeat()
                await self._file_lock.release()
                self._log(job, started, page)
        return job

    async def run_forever(self) -> None:
        recovered_lock = await self._file_lock.recover_stale_lock()
        if recovered_lock:
            self.logger.warning("Recovered stale browser lock", extra={
                "component": "browser_lock", "event": "STALE_BROWSER_LOCK_RECOVERED",
                "stale_path": str(recovered_lock),
            })
        await asyncio.to_thread(self.store.recover_interrupted)
        while not self._stopping:
            from_queue = False
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1)
                from_queue = True
            except TimeoutError:
                job = await asyncio.to_thread(self.store.next_pending, self.config.max_job_retries)
                if job is None:
                    continue
            try:
                await self.execute(job)
            finally:
                if from_queue:
                    self._queue.task_done()

    def stop(self) -> None:
        self._stopping = True

    async def _check_login(self, job: FacebookJob, page: Any) -> dict[str, Any]:
        await page.goto(
            "https://www.facebook.com/", wait_until="domcontentloaded",
            timeout=int(self.config.startup_timeout_seconds * 1000),
        )
        detection = await self._state_detector.detect(page)
        if detection.state is FacebookPageState.UNKNOWN:
            await self._state_detector.save_unknown_artifacts(
                page, detection, root=self.config.diagnostics_path,
                job_id=job.job_id, browser_profile=str(self.config.profile_path),
                browser_port=self.config.cdp_port,
            )
        return {
            "logged_in": detection.state is FacebookPageState.LOGGED_IN,
            "state": detection.state.value,
            "url": detection.url,
        }

    async def _engage_reel(self, job: FacebookJob, page: Any) -> dict[str, Any]:
        payload = job.payload
        return await self._engagement_service.like_reel_and_comments(
            page,
            str(payload["reel_url"]),
            like_reel=bool(payload.get("like_reel", True)),
            like_comments=bool(payload.get("like_comments", True)),
            like_replies=bool(payload.get("like_replies", False)),
        )

    async def _diagnose(self, job: FacebookJob, page: Any, stack: str) -> None:
        output = self.config.diagnostics_path / job.job_id
        output.mkdir(parents=True, exist_ok=True)
        (output / "error.txt").write_text(stack, encoding="utf-8")
        if page is not None and not page.is_closed():
            try:
                await self.manager.save_diagnostics(page, output, "failure")
            except Exception:
                self.logger.exception("Failed to capture Facebook job diagnostics", extra={"job_id": job.job_id})

    def _log(self, job: FacebookJob, started: float, page: Any = None) -> None:
        self.logger.info("Facebook browser job", extra={
            "job_id": job.job_id, "job_type": job.job_type.value, "framework": self.config.framework,
            "browser_process_id": self.manager.browser_process_id, "cdp_port": self.config.cdp_port,
            "profile_path": str(self.config.profile_path), "page_url": str(getattr(page, "url", "")),
            "status": job.status.value, "duration": round(time.monotonic() - started, 3),
            "retry_count": job.retry_count, "error_type": (job.error_message or "").split(":", 1)[0],
        })

    @staticmethod
    def _tab_name(job_type: FacebookJobType) -> str:
        if job_type in {
            FacebookJobType.DOWNLOAD_REEL,
            FacebookJobType.EXTRACT_REEL_METADATA,
            FacebookJobType.EXTRACT_COMMENTS,
            FacebookJobType.ENGAGE_REEL,
        }:
            return "facebook_reel"
        if job_type is FacebookJobType.JOIN_GROUP:
            return "facebook_group"
        if job_type in {FacebookJobType.SHARE_POST, FacebookJobType.CREATE_POST, FacebookJobType.COMMENT_POST}:
            return "facebook_post"
        return "facebook_main"
