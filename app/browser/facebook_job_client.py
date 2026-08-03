from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.browser.facebook_job import FacebookJob, FacebookJobStatus, FacebookJobStore, FacebookJobType
from app.config.facebook_browser import FacebookBrowserConfig
from app.config.settings import Settings


class FacebookJobClient:
    """Process-safe producer/poller; it never touches a browser."""

    def __init__(self, store: FacebookJobStore | None = None, *, poll_seconds: float = 0.25):
        config = FacebookBrowserConfig.from_settings(Settings.from_env())
        self.store = store or FacebookJobStore(config.queue_database_path)
        self.poll_seconds = poll_seconds

    def submit(self, job_type: FacebookJobType, payload: dict[str, Any], *, idempotency_key: str | None = None) -> FacebookJob:
        return self.store.create(FacebookJob(job_type=job_type, payload=payload, idempotency_key=idempotency_key))

    def wait(self, job_id: str, timeout_seconds: float = 180) -> FacebookJob:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            job = self.store.get(job_id)
            if job is None:
                raise LookupError(job_id)
            if job.status in {FacebookJobStatus.SUCCESS, FacebookJobStatus.FAILED, FacebookJobStatus.CANCELLED}:
                return job
            time.sleep(self.poll_seconds)
        raise TimeoutError(f"Facebook worker did not complete job {job_id} within {timeout_seconds}s")

    def execute(self, job_type: FacebookJobType, payload: dict[str, Any], *, timeout_seconds: float = 180, idempotency_key: str | None = None) -> Any:
        job = self.submit(job_type, payload, idempotency_key=idempotency_key)
        completed = self.wait(job.job_id, timeout_seconds)
        if completed.status is not FacebookJobStatus.SUCCESS:
            raise RuntimeError(completed.error_message or f"Facebook job {completed.status.value}")
        return completed.result
