"""Legacy synchronous browser-job store retained for compatibility only."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class FacebookJobType(StrEnum):
    DOWNLOAD_REEL = "DOWNLOAD_REEL"
    EXTRACT_REEL_METADATA = "EXTRACT_REEL_METADATA"
    EXTRACT_COMMENTS = "EXTRACT_COMMENTS"
    SHARE_POST = "SHARE_POST"
    CREATE_POST = "CREATE_POST"
    JOIN_GROUP = "JOIN_GROUP"
    COMMENT_POST = "COMMENT_POST"
    CHECK_LOGIN = "CHECK_LOGIN"
    COLLECT_PAGE_POSTS = "COLLECT_PAGE_POSTS"


class FacebookJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRY_WAITING = "RETRY_WAITING"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class FacebookJob:
    job_type: FacebookJobType
    payload: dict[str, Any]
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: FacebookJobStatus = FacebookJobStatus.PENDING
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    completed_at: str | None = None
    retry_count: int = 0
    error_message: str | None = None
    idempotency_key: str | None = None
    result: Any = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["job_type"] = self.job_type.value
        data["status"] = self.status.value
        return data


class FacebookJobStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS facebook_browser_jobs (
                    job_id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    idempotency_key TEXT UNIQUE,
                    result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_facebook_jobs_status_created
                    ON facebook_browser_jobs(status, created_at);
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def create(self, job: FacebookJob) -> FacebookJob:
        self.initialize()
        with self._connect() as db:
            try:
                db.execute("INSERT INTO facebook_browser_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                    job.job_id, job.job_type.value, json.dumps(job.payload, ensure_ascii=False), job.status.value,
                    job.created_at, job.started_at, job.completed_at, job.retry_count, job.error_message,
                    job.idempotency_key, json.dumps(job.result, ensure_ascii=False) if job.result is not None else None,
                ))
            except sqlite3.IntegrityError:
                if not job.idempotency_key:
                    raise
                existing = db.execute("SELECT * FROM facebook_browser_jobs WHERE idempotency_key=?", (job.idempotency_key,)).fetchone()
                return self._row(existing)
        return job

    def update(self, job: FacebookJob) -> None:
        with self._connect() as db:
            db.execute("""UPDATE facebook_browser_jobs SET status=?, started_at=?, completed_at=?, retry_count=?, error_message=?, result_json=? WHERE job_id=?""", (
                job.status.value, job.started_at, job.completed_at, job.retry_count, job.error_message,
                json.dumps(job.result, ensure_ascii=False) if job.result is not None else None, job.job_id,
            ))

    def get(self, job_id: str) -> FacebookJob | None:
        self.initialize()
        with self._connect() as db:
            row = db.execute("SELECT * FROM facebook_browser_jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def next_pending(self, max_retries: int = 3) -> FacebookJob | None:
        self.initialize()
        with self._connect() as db:
            row = db.execute("SELECT * FROM facebook_browser_jobs WHERE status IN (?, ?) AND retry_count <= ? ORDER BY created_at LIMIT 1", (FacebookJobStatus.PENDING.value, FacebookJobStatus.RETRY_WAITING.value, max_retries)).fetchone()
        return self._row(row) if row else None

    def recover_interrupted(self) -> int:
        self.initialize()
        with self._connect() as db:
            cursor = db.execute("""UPDATE facebook_browser_jobs SET status=?, retry_count=retry_count+1, error_message=? WHERE status=?""", (
                FacebookJobStatus.RETRY_WAITING.value, "Worker restarted while job was RUNNING", FacebookJobStatus.RUNNING.value,
            ))
            return cursor.rowcount

    @staticmethod
    def _row(row: sqlite3.Row) -> FacebookJob:
        return FacebookJob(
            job_id=row["job_id"], job_type=FacebookJobType(row["job_type"]), payload=json.loads(row["payload_json"]),
            status=FacebookJobStatus(row["status"]), created_at=row["created_at"], started_at=row["started_at"],
            completed_at=row["completed_at"], retry_count=row["retry_count"], error_message=row["error_message"],
            idempotency_key=row["idempotency_key"], result=json.loads(row["result_json"]) if row["result_json"] else None,
        )
