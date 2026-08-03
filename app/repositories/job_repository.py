"""Deprecated compatibility facade for the SQLite repository implementation."""

from app.infrastructure.persistence.sqlite_job_repository import (
    JobNotFoundError,
    JobRepository,
    SQLiteJobRepository,
)

__all__ = ["JobNotFoundError", "JobRepository", "SQLiteJobRepository"]
