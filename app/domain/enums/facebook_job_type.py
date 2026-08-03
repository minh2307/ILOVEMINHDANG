"""Compatibility import for the authoritative job type enum."""

from app.domain.enums.job_type import JobType

FacebookJobType = JobType

__all__ = ["FacebookJobType", "JobType"]
