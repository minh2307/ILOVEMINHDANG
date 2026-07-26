from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.models.workflow import JobEvent, JobRecord, WorkflowStatus
from app.workflows.state_machine import WorkflowStateMachine


class JobNotFoundError(LookupError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class JobRepository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path).resolve()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    normalized_source_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_source_url ON jobs(source_url);
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

                CREATE TABLE IF NOT EXISTS job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_job_events_job_id
                    ON job_events(job_id, event_id);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "normalized_source_url" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN normalized_source_url TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_normalized_source_url "
                "ON jobs(normalized_source_url, created_at)"
            )
            connection.commit()

    def backup_database(self) -> tuple["Path", dict]:
        """Create a timestamped backup of the current SQLite database.

        Returns (backup_path, info_dict).
        Raises FileNotFoundError when the original database does not exist.
        Raises RuntimeError when backup verification fails.
        """
        import shutil
        if not self.database_path.exists():
            raise FileNotFoundError(f"Database not found: {self.database_path}")
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.database_path.with_name(
            f"{self.database_path.stem}_backup_{timestamp}{self.database_path.suffix}"
        )
        shutil.copy2(self.database_path, backup_path)
        if not backup_path.exists() or backup_path.stat().st_size == 0:
            raise RuntimeError(f"Backup verification failed: {backup_path}")
        with self._connection() as connection:
            job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            event_count = connection.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]
        return backup_path, {
            "original": str(self.database_path),
            "backup": str(backup_path),
            "backup_size_bytes": backup_path.stat().st_size,
            "job_count": job_count,
            "event_count": event_count,
            "created_at": timestamp,
        }

    def create_job(
        self,
        source_url: str,
        *,
        job_id: str | None = None,
        normalized_source_url: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> JobRecord:
        source_url = source_url.strip()
        if not source_url:
            raise ValueError("source_url cannot be empty")
        identifier = job_id or uuid.uuid4().hex
        now = _utc_now()
        payload = data or {}
        normalized_source = (normalized_source_url or source_url).strip()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO jobs(
                    job_id, source_url, normalized_source_url, status,
                    data_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    source_url,
                    normalized_source,
                    WorkflowStatus.CREATED.value,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_events(job_id, from_status, to_status, details_json, created_at)
                VALUES (?, NULL, ?, ?, ?)
                """,
                (
                    identifier,
                    WorkflowStatus.CREATED.value,
                    json.dumps({"reason": "job_created"}),
                    now,
                ),
            )
            connection.commit()
        return JobRecord(
            identifier,
            source_url,
            WorkflowStatus.CREATED,
            normalized_source,
            payload,
            now,
            now,
        )

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def find_latest_by_source_url(self, source_url: str) -> JobRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE source_url = ? ORDER BY created_at DESC LIMIT 1",
                (source_url.strip(),),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def find_latest_by_normalized_source_url(self, normalized_source_url: str) -> JobRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE normalized_source_url = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (normalized_source_url.strip(),),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def find_facebook_duplicates(
        self, content_hash: str, target_url: str, *, exclude_job_id: str = ""
    ) -> list[JobRecord]:
        """Return jobs whose persisted Facebook fingerprint and target match."""
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        matches: list[JobRecord] = []
        for row in rows:
            if row["job_id"] == exclude_job_id:
                continue
            job = self._row_to_job(row)
            if (
                job.data.get("facebook_content_hash") == content_hash
                and job.data.get("facebook_target_url") == target_url
            ):
                matches.append(job)
        return matches

    def list_jobs(self, limit: int = 100) -> list[JobRecord]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def list_resumable_jobs(self, limit: int = 100) -> list[dict]:
        """Return jobs that can be resumed with their recommended next action."""
        _RESUMABLE_STATUSES = {
            "DOWNLOADED", "CLINICAL_FACTORS_GENERATED", "CDHA_ANALYZED",
            "SCREENSHOTS_CAPTURED", "WAITING_FOR_REVIEW", "APPROVED",
            "FACEBOOK_WAITING_FOR_MANUAL_REVIEW", "FACEBOOK_PUBLISHED",
            "POST_URL_EXTRACTED", "COMMENT_ADDED", "FACEBOOK_PUBLISH_FAILED",
            "POST_URL_EXTRACTION_FAILED", "COMMENT_FAILED", "RETRY_PENDING",
            "DOWNLOADREEL_FAILED", "GEMINI_FAILED", "AI_FAILED", "CDHA_FAILED",
        }
        _PENDING_ACTIONS = {
            "WAITING_FOR_REVIEW": "python main.py --review-job {job_id}",
            "APPROVED": "python main.py --complete-facebook {job_id}",
            "FACEBOOK_WAITING_FOR_MANUAL_REVIEW": "python main.py --publish-facebook {job_id}",
            "FACEBOOK_PUBLISHED": "python main.py --extract-facebook-link {job_id}",
            "POST_URL_EXTRACTED": "python main.py --comment-facebook-link {job_id}",
        }
        jobs = self.list_jobs(limit)
        result = []
        for job in jobs:
            if job.status.value not in _RESUMABLE_STATUSES:
                continue
            pending = _PENDING_ACTIONS.get(job.status.value, f"python main.py --resume-job {job.job_id}")
            result.append({
                "job_id": job.job_id,
                "status": job.status.value,
                "source_url": job.source_url[:60] + ("..." if len(job.source_url) > 60 else ""),
                "updated_at": job.updated_at,
                "recommended_command": pending.format(job_id=job.job_id),
            })
        return result

    def transition(
        self,
        job_id: str,
        target: WorkflowStatus,
        *,
        details: dict[str, Any] | None = None,
        data_patch: dict[str, Any] | None = None,
    ) -> JobRecord:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise JobNotFoundError(job_id)
            current = WorkflowStatus(row["status"])
            WorkflowStateMachine.validate(current, target)
            payload = json.loads(row["data_json"] or "{}")
            if data_patch:
                payload.update(data_patch)
            connection.execute(
                "UPDATE jobs SET status = ?, data_json = ?, updated_at = ? WHERE job_id = ?",
                (target.value, json.dumps(payload, ensure_ascii=False), now, job_id),
            )
            connection.execute(
                """
                INSERT INTO job_events(job_id, from_status, to_status, details_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    current.value,
                    target.value,
                    json.dumps(details or {}, ensure_ascii=False),
                    now,
                ),
            )
            connection.commit()
            created_at = row["created_at"]
            source_url = row["source_url"]
        return JobRecord(
            job_id,
            source_url,
            target,
            row["normalized_source_url"],
            payload,
            created_at,
            now,
        )

    def record_event(
        self, job_id: str, *, details: dict[str, Any]
    ) -> JobEvent:
        """Append an audit event without changing workflow state."""
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise JobNotFoundError(job_id)
            status = WorkflowStatus(row["status"])
            cursor = connection.execute(
                """
                INSERT INTO job_events(job_id, from_status, to_status, details_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, status.value, status.value, json.dumps(details, ensure_ascii=False), now),
            )
            connection.commit()
            event_id = int(cursor.lastrowid)
        return JobEvent(event_id, job_id, status, status, details, now)
    def record_error(
        self,
        job_id: str,
        error: "PipelineError",
        *,
        attempt: int | None = None,
        browser_url: str | None = None,
        selector_key: str | None = None,
    ) -> JobEvent:
        """Append a structured, redacted error event without changing status."""
        from app.error_events import build_error_event_details

        return self.record_event(
            job_id,
            details=build_error_event_details(
                error,
                attempt=attempt,
                browser_url=browser_url,
                selector_key=selector_key,
            ),
        )


    def update_data(self, job_id: str, data_patch: dict[str, Any]) -> JobRecord:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise JobNotFoundError(job_id)
            payload = json.loads(row["data_json"] or "{}")
            payload.update(data_patch)
            connection.execute(
                "UPDATE jobs SET data_json = ?, updated_at = ? WHERE job_id = ?",
                (json.dumps(payload, ensure_ascii=False), now, job_id),
            )
            connection.commit()
        return JobRecord(
            job_id,
            row["source_url"],
            WorkflowStatus(row["status"]),
            row["normalized_source_url"],
            payload,
            row["created_at"],
            now,
        )

    def list_events(self, job_id: str) -> list[JobEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id = ? ORDER BY event_id", (job_id,)
            ).fetchall()
        return [
            JobEvent(
                event_id=row["event_id"],
                job_id=row["job_id"],
                from_status=WorkflowStatus(row["from_status"]) if row["from_status"] else None,
                to_status=WorkflowStatus(row["to_status"]),
                details=json.loads(row["details_json"] or "{}"),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            source_url=row["source_url"],
            status=WorkflowStatus(row["status"]),
            normalized_source_url=row["normalized_source_url"],
            data=json.loads(row["data_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
