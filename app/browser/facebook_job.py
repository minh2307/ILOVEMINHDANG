"""Deprecated compatibility imports for the isolated browser-job store."""

from app.infrastructure.legacy.facebook_browser.job_store import (
    FacebookJob,
    FacebookJobStatus,
    FacebookJobStore,
    FacebookJobType,
    utc_now,
)

__all__ = [
    "FacebookJob",
    "FacebookJobStatus",
    "FacebookJobStore",
    "FacebookJobType",
    "utc_now",
]
