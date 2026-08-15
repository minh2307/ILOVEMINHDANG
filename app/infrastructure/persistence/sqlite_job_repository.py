from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.domain.enums.job_status import JobStatus as WorkflowStatus
from app.domain.enums.job_type import JobType
from app.domain.enums.facebook_publication_state import FacebookPublicationState
from app.domain.models.job import Job as JobRecord
from app.domain.models.job_event import JobEvent
from app.domain.policies.external_side_effect_policy import (
    FacebookSubmissionEvidence,
    build_facebook_submission_evidence,
)
from app.domain.exceptions.errors import InvalidTransitionError
from app.domain.rules.state_transitions import JobStateTransitions as WorkflowStateMachine
from app.domain.rules.facebook_publication_state_machine import (
    FacebookPublicationStateMachine,
)


class JobNotFoundError(LookupError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _artifact_paths(existing_json: str | None, patch: dict[str, Any]) -> list[str]:
    """Collect explicit artifact fields without treating URLs as filesystem paths."""
    existing = json.loads(existing_json or "[]")
    values = [str(value) for value in existing if value]
    for key, value in patch.items():
        if key.endswith("_path") and isinstance(value, (str, Path)) and value:
            values.append(str(value))
        elif key.endswith("_paths") and isinstance(value, (list, tuple)):
            values.extend(str(item) for item in value if item)
    return list(dict.fromkeys(values))


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
                    job_type TEXT NOT NULL DEFAULT 'PROCESS_WORKFLOW',
                    status TEXT NOT NULL,
                    previous_status TEXT,
                    input_payload_json TEXT NOT NULL DEFAULT '{}',
                    output_payload_json TEXT NOT NULL DEFAULT '{}',
                    artifact_paths_json TEXT NOT NULL DEFAULT '[]',
                    error_code TEXT,
                    error_message TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    publish_attempts INTEGER NOT NULL DEFAULT 0,
                    reconciliation_attempts INTEGER NOT NULL DEFAULT 0,
                    claimed_by TEXT,
                    lease_expires_at TEXT,
                    last_heartbeat TEXT,
                    completed_at TEXT,
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
                    event_type TEXT NOT NULL DEFAULT 'JOB_STATE_CHANGED',
                    attempt INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_job_events_job_id
                    ON job_events(job_id, event_id);
                
                CREATE TABLE IF NOT EXISTS facebook_publication_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    target_url TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    worker_id TEXT,
                    caption_hash TEXT,
                    media_hashes_json TEXT NOT NULL DEFAULT '[]',
                    post_id TEXT,
                    permalink TEXT,
                    verification_method TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    diagnostic_paths_json TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_fb_attempts_job_id
                    ON facebook_publication_attempts(job_id);
                CREATE INDEX IF NOT EXISTS idx_fb_attempts_fingerprint
                    ON facebook_publication_attempts(content_fingerprint);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "normalized_source_url" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN normalized_source_url TEXT NOT NULL DEFAULT ''"
                )
            job_migrations = {
                "job_type": "TEXT NOT NULL DEFAULT 'PROCESS_WORKFLOW'",
                "previous_status": "TEXT",
                "input_payload_json": "TEXT NOT NULL DEFAULT '{}'",
                "output_payload_json": "TEXT NOT NULL DEFAULT '{}'",
                "artifact_paths_json": "TEXT NOT NULL DEFAULT '[]'",
                "error_code": "TEXT",
                "error_message": "TEXT",
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "max_attempts": "INTEGER NOT NULL DEFAULT 3",
                "publish_attempts": "INTEGER NOT NULL DEFAULT 0",
                "reconciliation_attempts": "INTEGER NOT NULL DEFAULT 0",
                "claimed_by": "TEXT",
                "lease_expires_at": "TEXT",
                "last_heartbeat": "TEXT",
                "completed_at": "TEXT",
            }
            for name, definition in job_migrations.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_normalized_source_url "
                "ON jobs(normalized_source_url, created_at)"
            )
            event_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(job_events)").fetchall()
            }
            if "event_type" not in event_columns:
                connection.execute(
                    "ALTER TABLE job_events ADD COLUMN event_type TEXT NOT NULL "
                    "DEFAULT 'JOB_STATE_CHANGED'"
                )
            if "attempt" not in event_columns:
                connection.execute(
                    "ALTER TABLE job_events ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                UPDATE jobs
                SET publish_attempts = MAX(
                    publish_attempts,
                    (SELECT COUNT(*) FROM job_events e
                     WHERE e.job_id = jobs.job_id
                       AND e.event_type = 'FACEBOOK_SUBMITTING'),
                    (SELECT COUNT(*) FROM facebook_publication_attempts a
                     WHERE a.job_id = jobs.job_id
                       AND (a.status IN ('SUBMITTING', 'SUBMITTED_UNCONFIRMED',
                                         'UNCERTAIN', 'VERIFIED')
                            OR lower(COALESCE(a.error_message, ''))
                               LIKE '%publication outcome is uncertain%'))
                ),
                reconciliation_attempts = MAX(
                    reconciliation_attempts,
                    (SELECT COUNT(*) FROM job_events e
                     WHERE e.job_id = jobs.job_id
                       AND e.event_type =
                           'FACEBOOK_PUBLICATION_RECONCILIATION_STARTED'),
                    (SELECT COUNT(*) FROM job_events e
                     WHERE e.job_id = jobs.job_id
                       AND e.event_type = 'reconciliation_started')
                )
                """
            )
            connection.commit()

    def backup_database(self) -> tuple["Path", dict]:
        """Create a timestamped backup of the current SQLite database.

        Returns (backup_path, info_dict).
        Raises FileNotFoundError when the original database does not exist.
        Raises RuntimeError when backup verification fails.
        """
        if not self.database_path.exists():
            raise FileNotFoundError(f"Database not found: {self.database_path}")
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.database_path.with_name(
            f"{self.database_path.stem}_backup_{timestamp}{self.database_path.suffix}"
        )
        if backup_path.exists():
            raise FileExistsError(f"Backup already exists: {backup_path}")
        source = sqlite3.connect(
            f"file:{self.database_path}?mode=ro", uri=True, timeout=30
        )
        destination = sqlite3.connect(backup_path, timeout=30)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        if not backup_path.exists() or backup_path.stat().st_size == 0:
            raise RuntimeError(f"Backup verification failed: {backup_path}")
        with sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True) as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"Backup integrity check failed: {integrity}")
            job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            event_count = connection.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]
        return backup_path, {
            "original": str(self.database_path),
            "backup": str(backup_path),
            "backup_size_bytes": backup_path.stat().st_size,
            "job_count": job_count,
            "event_count": event_count,
            "quick_check": integrity,
            "created_at": timestamp,
        }

    def create_job(
        self,
        source_url: str,
        *,
        job_id: str | None = None,
        normalized_source_url: str | None = None,
        data: dict[str, Any] | None = None,
        job_type: JobType = JobType.PROCESS_WORKFLOW,
        input_payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
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
                    job_id, source_url, normalized_source_url, job_type, status,
                    input_payload_json, data_json, max_attempts, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    source_url,
                    normalized_source,
                    job_type.value,
                    WorkflowStatus.CREATED.value,
                    json.dumps(input_payload or {"source_url": source_url}, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    max(1, int(max_attempts)),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_events(
                    job_id, from_status, to_status, details_json, created_at, event_type, attempt
                ) VALUES (?, NULL, ?, ?, ?, 'JOB_STATE_CHANGED', 0)
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
            job_type=job_type,
            input_payload=input_payload or {"source_url": source_url},
            max_attempts=max(1, int(max_attempts)),
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

    def list_jobs_by_status(
        self, statuses: set[WorkflowStatus], *, limit: int = 100
    ) -> list[JobRecord]:
        if not statuses:
            return []
        if limit < 1:
            raise ValueError("limit must be positive")
        values = sorted(status.value for status in statuses)
        placeholders = ",".join("?" for _ in values)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs WHERE status IN ({placeholders}) "
                "ORDER BY updated_at, job_id LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def list_resumable_jobs(self, limit: int = 100) -> list[dict]:
        """Return jobs that can be resumed with their recommended next action."""
        _RESUMABLE_STATUSES = {
            "DOWNLOADED", "CLINICAL_FACTORS_GENERATED", "CDHA_ANALYZED",
            "SCREENSHOTS_CAPTURED", "WAITING_FOR_REVIEW", "APPROVED",
            "FACEBOOK_WAITING_FOR_MANUAL_REVIEW", "FACEBOOK_PUBLISHED",
            "POST_URL_EXTRACTED", "COMMENT_ADDED", "FACEBOOK_PUBLISH_FAILED",
            "FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED", "SCREENSHOTS_FAILED",
            "BLOCKED_USER_APPROVAL",
            "POST_URL_EXTRACTION_FAILED", "COMMENT_FAILED", "RETRY_PENDING",
            "DOWNLOADREEL_FAILED", "GEMINI_FAILED", "AI_FAILED", "CDHA_FAILED",
            "WAITING_FOR_AUTH_REVIEW", "FACEBOOK_PUBLISHING", "RETRYABLE", "BLOCKED",
        }
        _PENDING_ACTIONS = {
            "WAITING_FOR_REVIEW": "python main.py review --job-id {job_id}",
            "APPROVED": "python main.py resume --job-id {job_id}",
            "FACEBOOK_WAITING_FOR_MANUAL_REVIEW": "python main.py confirm-publish --job-id {job_id}",
            "FACEBOOK_PUBLISHED": "python main.py resume --job-id {job_id}",
            "POST_URL_EXTRACTED": "python main.py resume --job-id {job_id}",
            "WAITING_FOR_AUTH_REVIEW": "python main.py resume --job-id {job_id}",
            "FACEBOOK_PUBLISHING": "python main.py resume --job-id {job_id}",
        }
        jobs = self.list_jobs(limit)
        result = []
        for job in jobs:
            if job.status.value not in _RESUMABLE_STATUSES:
                continue
            pending = _PENDING_ACTIONS.get(job.status.value, f"python main.py resume --job-id {job.job_id}")
            result.append({
                "job_id": job.job_id,
                "status": job.status.value,
                "source_url": job.source_url[:60] + ("..." if len(job.source_url) > 60 else ""),
                "updated_at": job.updated_at,
                "recommended_command": pending.format(job_id=job.job_id),
            })
        return result

    @staticmethod
    def _submission_evidence_with_connection(
        connection: sqlite3.Connection, job_id: str
    ) -> FacebookSubmissionEvidence:
        job_row = connection.execute(
            "SELECT data_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if job_row is None:
            raise JobNotFoundError(job_id)
        event_rows = connection.execute(
            "SELECT event_type, details_json, created_at FROM job_events "
            "WHERE job_id = ? ORDER BY event_id",
            (job_id,),
        ).fetchall()
        events = [
            {
                "event_type": row["event_type"],
                "details": json.loads(row["details_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in event_rows
        ]
        attempts = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM facebook_publication_attempts "
                "WHERE job_id = ? ORDER BY started_at, attempt_id",
                (job_id,),
            ).fetchall()
        ]
        return build_facebook_submission_evidence(
            json.loads(job_row["data_json"] or "{}"),
            events=events,
            publication_attempts=attempts,
        )

    def get_facebook_submission_evidence(
        self, job_id: str
    ) -> FacebookSubmissionEvidence:
        with self._connection() as connection:
            return self._submission_evidence_with_connection(connection, job_id)

    def transition(
        self,
        job_id: str,
        target: WorkflowStatus,
        *,
        details: dict[str, Any] | None = None,
        data_patch: dict[str, Any] | None = None,
        event_type: str = "JOB_STATE_CHANGED",
        attempt: int = 0,
    ) -> JobRecord:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                connection.rollback()
                raise JobNotFoundError(job_id)
            current = WorkflowStatus(row["status"])
            transition_details = details or {}
            evidence = self._submission_evidence_with_connection(connection, job_id)
            retry_step = str(
                transition_details.get("retry_step")
                or (data_patch or {}).get("retry_step")
                or ""
            )
            unsafe_after_submit = target in {
                WorkflowStatus.APPROVED,
                WorkflowStatus.FACEBOOK_PREPARING,
                WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
                WorkflowStatus.FACEBOOK_PUBLISHING,
            } or (
                target is WorkflowStatus.RETRY_PENDING
                and retry_step == "facebook_prepare"
            )
            if evidence.committed and unsafe_after_submit:
                connection.rollback()
                raise InvalidTransitionError(
                    "Blocked by durable Facebook submit evidence: "
                    f"job_id={job_id}, publish_attempts={evidence.publish_attempts}, "
                    f"requested_state={target.value}"
                )
            WorkflowStateMachine.validate(
                current,
                target,
                job_id=job_id,
                reason=str(
                    transition_details.get("reason") or transition_details.get("error") or ""
                ),
            )
            payload = json.loads(row["data_json"] or "{}")
            if data_patch:
                payload.update(data_patch)
            artifacts = _artifact_paths(row["artifact_paths_json"], data_patch or {})
            connection.execute(
                """
                UPDATE jobs
                SET previous_status = ?, status = ?, data_json = ?,
                    output_payload_json = ?, artifact_paths_json = ?,
                    error_code = ?, error_message = ?,
                    attempt_count = MAX(attempt_count, ?), completed_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    current.value,
                    target.value,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(artifacts, ensure_ascii=False),
                    (details or {}).get("error_code") if target.value.endswith("FAILED") else None,
                    (details or {}).get("error") if target.value.endswith("FAILED") else None,
                    max(0, int(attempt)),
                    now if target is WorkflowStatus.COMPLETED else None,
                    now,
                    job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_events(
                    job_id, from_status, to_status, details_json, created_at, event_type, attempt
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    current.value,
                    target.value,
                    json.dumps(details or {}, ensure_ascii=False),
                    now,
                    event_type,
                    attempt,
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
            job_type=JobType(row["job_type"]),
            previous_status=current,
            input_payload=json.loads(row["input_payload_json"] or "{}"),
            output_payload=payload,
            artifact_paths=artifacts,
            error_code=(details or {}).get("error_code") if target.value.endswith("FAILED") else None,
            error_message=(details or {}).get("error") if target.value.endswith("FAILED") else None,
            attempt_count=max(int(row["attempt_count"] or 0), int(attempt)),
            max_attempts=int(row["max_attempts"] or 3),
            claimed_by=row["claimed_by"],
            lease_expires_at=row["lease_expires_at"],
            last_heartbeat=row["last_heartbeat"],
            completed_at=now if target is WorkflowStatus.COMPLETED else None,
            publish_attempts=int(row["publish_attempts"] or 0),
            reconciliation_attempts=int(row["reconciliation_attempts"] or 0),
        )

    def record_event(
        self, job_id: str, *, details: dict[str, Any],
        event_type: str = "JOB_STATE_CHANGED", attempt: int = 0,
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
            if event_type == "FACEBOOK_SUBMITTING":
                connection.execute(
                    "UPDATE jobs SET publish_attempts = publish_attempts + 1, "
                    "updated_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
            elif event_type in {
                "FACEBOOK_PUBLICATION_RECONCILIATION_STARTED",
                "reconciliation_started",
            }:
                connection.execute(
                    "UPDATE jobs SET reconciliation_attempts = "
                    "reconciliation_attempts + 1, updated_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
            cursor = connection.execute(
                """
                INSERT INTO job_events(
                    job_id, from_status, to_status, details_json, created_at, event_type, attempt
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, status.value, status.value, json.dumps(details, ensure_ascii=False),
                 now, event_type, attempt),
            )
            connection.commit()
            event_id = int(cursor.lastrowid)
        return JobEvent(event_id, job_id, status, status, details, now, event_type, attempt)
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
            event_type="PIPELINE_ERROR",
            attempt=attempt or 0,
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
            artifacts = _artifact_paths(row["artifact_paths_json"], data_patch)
            connection.execute(
                "UPDATE jobs SET data_json = ?, output_payload_json = ?, "
                "artifact_paths_json = ?, updated_at = ? "
                "WHERE job_id = ?",
                (
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(artifacts, ensure_ascii=False),
                    now,
                    job_id,
                ),
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
            job_type=JobType(row["job_type"]),
            previous_status=(
                WorkflowStatus(row["previous_status"]) if row["previous_status"] else None
            ),
            input_payload=json.loads(row["input_payload_json"] or "{}"),
            output_payload=payload,
            artifact_paths=artifacts,
            error_code=row["error_code"],
            error_message=row["error_message"],
            attempt_count=int(row["attempt_count"] or 0),
            max_attempts=int(row["max_attempts"] or 3),
            claimed_by=row["claimed_by"],
            lease_expires_at=row["lease_expires_at"],
            last_heartbeat=row["last_heartbeat"],
            completed_at=row["completed_at"],
            publish_attempts=int(row["publish_attempts"] or 0),
            reconciliation_attempts=int(row["reconciliation_attempts"] or 0),
        )
    def mark_facebook_submitting(
        self,
        job_id: str,
        *,
        submitted_at: str,
        content_fingerprint: str,
        target_url: str,
    ) -> JobRecord:
        """Persist the crash-safe pre-click checkpoint in one transaction."""

        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise JobNotFoundError(job_id)
            evidence = self._submission_evidence_with_connection(connection, job_id)
            if evidence.committed:
                connection.rollback()
                raise InvalidTransitionError(
                    "A durable Facebook submit checkpoint already exists; "
                    f"second publish blocked for job_id={job_id}"
                )
            payload = json.loads(row["data_json"] or "{}")
            raw_state = str(
                payload.get("facebook_publication_state") or "FAILED_BEFORE_SUBMIT"
            ).upper()
            try:
                current_state = FacebookPublicationState(raw_state)
            except ValueError:
                current_state = FacebookPublicationState.FAILED_BEFORE_SUBMIT
            FacebookPublicationStateMachine.validate(
                current_state,
                FacebookPublicationState.SUBMITTING,
                job_id=job_id,
            )
            payload.update(
                {
                    "facebook_submission_status": "SUBMITTING",
                    "facebook_publication_state": "SUBMITTING",
                    "facebook_submitted_at": submitted_at,
                    "facebook_submit_timestamp": submitted_at,
                    "facebook_content_hash": content_fingerprint,
                    "facebook_target_url": target_url,
                }
            )
            connection.execute(
                "UPDATE jobs SET data_json = ?, output_payload_json = ?, "
                "publish_attempts = publish_attempts + 1, updated_at = ? "
                "WHERE job_id = ?",
                (
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    job_id,
                ),
            )
            attempt_id = str(payload.get("facebook_attempt_id") or "").strip()
            if attempt_id:
                connection.execute(
                    "UPDATE facebook_publication_attempts "
                    "SET status = 'SUBMITTING', updated_at = ? WHERE attempt_id = ?",
                    (now, attempt_id),
                )
            connection.execute(
                """
                INSERT INTO job_events(
                    job_id, from_status, to_status, details_json, created_at,
                    event_type, attempt
                ) VALUES (?, ?, ?, ?, ?, 'FACEBOOK_SUBMITTING', 0)
                """,
                (
                    job_id,
                    row["status"],
                    row["status"],
                    json.dumps(
                        {
                            "timestamp": submitted_at,
                            "submitted_at": submitted_at,
                            "submission_status": "SUBMITTING",
                            "content_fingerprint": content_fingerprint,
                            "target_url": target_url,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            connection.commit()
        return self.get_job(job_id)  # type: ignore[return-value]

    def begin_facebook_reconciliation(self, job_id: str) -> int:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, data_json, reconciliation_attempts FROM jobs "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise JobNotFoundError(job_id)
            evidence = self._submission_evidence_with_connection(connection, job_id)
            reconciliation_statuses = {
                WorkflowStatus.FACEBOOK_PUBLISHING.value,
                WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN.value,
                WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED.value,
                WorkflowStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED.value,
            }
            if not evidence.committed and row["status"] not in reconciliation_statuses:
                connection.rollback()
                raise InvalidTransitionError(
                    f"Reconciliation requires durable submit evidence: job_id={job_id}"
                )
            if evidence.possible_duplicate:
                connection.rollback()
                raise InvalidTransitionError(
                    f"Duplicate evidence requires manual review: job_id={job_id}"
                )
            count = int(row["reconciliation_attempts"] or 0) + 1
            payload = json.loads(row["data_json"] or "{}")
            payload.update(
                {
                    "facebook_reconciliation_attempt": count,
                    "facebook_reconciliation_last_started_at": now,
                    "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
                    "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
                }
            )
            connection.execute(
                "UPDATE jobs SET reconciliation_attempts = ?, data_json = ?, "
                "output_payload_json = ?, updated_at = ? WHERE job_id = ?",
                (
                    count,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_events(
                    job_id, from_status, to_status, details_json, created_at,
                    event_type, attempt
                ) VALUES (?, ?, ?, ?, ?, 'reconciliation_started', ?)
                """,
                (
                    job_id,
                    row["status"],
                    row["status"],
                    json.dumps(
                        {
                            "attempt": count,
                            "publish_clicked": False,
                            "submitted_at": evidence.submitted_at,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                    count,
                ),
            )
            connection.commit()
        return count

    def enforce_facebook_submission_guard(self, job_id: str) -> JobRecord:
        """Repair a tampered workflow status from immutable submit history."""

        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise JobNotFoundError(job_id)
            evidence = self._submission_evidence_with_connection(connection, job_id)
            if not evidence.committed:
                connection.rollback()
                return self._row_to_job(row)
            current = WorkflowStatus(row["status"])
            if evidence.possible_duplicate:
                target = WorkflowStatus.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW
            elif evidence.publication_state is FacebookPublicationState.PUBLISHED_CONFIRMED:
                unsafe = current in {
                    WorkflowStatus.CREATED,
                    WorkflowStatus.PENDING,
                    WorkflowStatus.APPROVED,
                    WorkflowStatus.FACEBOOK_PREPARING,
                    WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
                    WorkflowStatus.FACEBOOK_PUBLISHING,
                    WorkflowStatus.FACEBOOK_PUBLISH_FAILED,
                    WorkflowStatus.RETRY_PENDING,
                    WorkflowStatus.RETRYABLE,
                }
                target = WorkflowStatus.FACEBOOK_PUBLISHED if unsafe else current
            else:
                safe = {
                    WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
                    WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED,
                    WorkflowStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED,
                    WorkflowStatus.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW,
                    WorkflowStatus.FACEBOOK_PUBLISHED,
                    WorkflowStatus.POST_URL_EXTRACTING,
                    WorkflowStatus.POST_URL_EXTRACTED,
                    WorkflowStatus.COMMENT_ADDING,
                    WorkflowStatus.COMMENT_ADDED,
                    WorkflowStatus.COMPLETED,
                }
                target = (
                    current
                    if current in safe
                    else WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
                )
            if target is current:
                connection.rollback()
                return self._row_to_job(row)
            payload = json.loads(row["data_json"] or "{}")
            payload.update(
                {
                    "facebook_publication_state": evidence.publication_state.value,
                    "facebook_submission_status": (
                        "VERIFIED"
                        if evidence.publication_state
                        is FacebookPublicationState.PUBLISHED_CONFIRMED
                        else "SUBMITTED_UNCONFIRMED"
                    ),
                    "facebook_submitted_at": evidence.submitted_at,
                    "facebook_content_hash": evidence.content_fingerprint,
                    "facebook_target_url": evidence.target_url,
                    "facebook_guard_reason": "durable_submit_history_overrode_mutable_status",
                }
            )
            connection.execute(
                "UPDATE jobs SET previous_status = ?, status = ?, data_json = ?, "
                "output_payload_json = ?, completed_at = NULL, updated_at = ? "
                "WHERE job_id = ?",
                (
                    current.value,
                    target.value,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_events(
                    job_id, from_status, to_status, details_json, created_at,
                    event_type, attempt
                ) VALUES (?, ?, ?, ?, ?, 'FACEBOOK_SUBMIT_GUARD_ENFORCED', 0)
                """,
                (
                    job_id,
                    current.value,
                    target.value,
                    json.dumps(
                        {
                            "reason": "durable_submit_history_overrode_mutable_status",
                            "publish_attempts": evidence.publish_attempts,
                            "submitted_at": evidence.submitted_at,
                            "content_fingerprint": evidence.content_fingerprint,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            connection.commit()
        return self.get_job(job_id)  # type: ignore[return-value]

    def quarantine_possible_duplicate(
        self,
        job_id: str,
        *,
        expected_fingerprint: str,
        reason: str,
        matching_permalinks: list[str] | None = None,
    ) -> JobRecord:
        """Quarantine exactly one evidenced duplicate without touching its peers."""

        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise JobNotFoundError(job_id)
            evidence = self._submission_evidence_with_connection(connection, job_id)
            if evidence.content_fingerprint != expected_fingerprint:
                connection.rollback()
                raise ValueError(
                    "Facebook fingerprint mismatch; refusing scoped quarantine"
                )
            unique_matches = list(dict.fromkeys(matching_permalinks or []))
            if not evidence.possible_duplicate and len(unique_matches) < 2:
                connection.rollback()
                raise ValueError(
                    "At least two durable Facebook submit attempts are required"
                )
            current = WorkflowStatus(row["status"])
            target = WorkflowStatus.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW
            if current is target:
                connection.rollback()
                return self._row_to_job(row)
            payload = json.loads(row["data_json"] or "{}")
            payload.update(
                {
                    "facebook_publication_state": target.value,
                    "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
                    "facebook_submitted_at": evidence.submitted_at,
                    "facebook_content_hash": evidence.content_fingerprint,
                    "facebook_target_url": evidence.target_url,
                    "facebook_possible_duplicate_reason": reason,
                    "facebook_matching_permalinks": unique_matches,
                    "facebook_automation_blocked": True,
                }
            )
            connection.execute(
                "UPDATE jobs SET previous_status = ?, status = ?, data_json = ?, "
                "output_payload_json = ?, error_code = NULL, error_message = NULL, "
                "completed_at = NULL, updated_at = ? WHERE job_id = ?",
                (
                    current.value,
                    target.value,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_events(
                    job_id, from_status, to_status, details_json, created_at,
                    event_type, attempt
                ) VALUES (?, ?, ?, ?, ?,
                          'POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW', 0)
                """,
                (
                    job_id,
                    current.value,
                    target.value,
                    json.dumps(
                        {
                            "reason": reason,
                            "publish_attempts": evidence.publish_attempts,
                            "submitted_at": evidence.submitted_at,
                            "content_fingerprint": evidence.content_fingerprint,
                            "target_url": evidence.target_url,
                            "matching_permalinks": unique_matches,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            connection.commit()
        return self.get_job(job_id)  # type: ignore[return-value]

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
                event_type=row["event_type"],
                attempt=int(row["attempt"] or 0),
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
            job_type=JobType(row["job_type"]),
            previous_status=(
                WorkflowStatus(row["previous_status"]) if row["previous_status"] else None
            ),
            input_payload=json.loads(row["input_payload_json"] or "{}"),
            output_payload=json.loads(row["output_payload_json"] or "{}"),
            artifact_paths=json.loads(row["artifact_paths_json"] or "[]"),
            error_code=row["error_code"],
            error_message=row["error_message"],
            attempt_count=int(row["attempt_count"] or 0),
            max_attempts=int(row["max_attempts"] or 3),
            claimed_by=row["claimed_by"],
            lease_expires_at=row["lease_expires_at"],
            last_heartbeat=row["last_heartbeat"],
            completed_at=row["completed_at"],
            publish_attempts=int(row["publish_attempts"] or 0),
            reconciliation_attempts=int(row["reconciliation_attempts"] or 0),
        )

    def create_publication_attempt(
        self,
        job_id: str,
        content_fingerprint: str,
        target_url: str,
        *,
        status: str = "CREATED",
        worker_id: str | None = None,
        caption_hash: str | None = None,
        media_hashes: list[str] | None = None,
    ) -> str:
        attempt_id = uuid.uuid4().hex
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO facebook_publication_attempts (
                    attempt_id, job_id, content_fingerprint, status, target_url,
                    started_at, updated_at, worker_id, caption_hash, media_hashes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id, job_id, content_fingerprint, status, target_url,
                    now, now, worker_id, caption_hash,
                    json.dumps(media_hashes or []),
                )
            )
            connection.commit()
        return attempt_id

    def update_publication_attempt(
        self,
        attempt_id: str,
        *,
        status: str | None = None,
        post_id: str | None = None,
        permalink: str | None = None,
        verification_method: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        diagnostic_paths: list[str] | None = None,
        completed: bool = False,
    ) -> None:
        now = _utc_now()
        updates: list[str] = ["updated_at = ?"]
        params: list[Any] = [now]
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if post_id is not None:
            updates.append("post_id = ?")
            params.append(post_id)
        if permalink is not None:
            updates.append("permalink = ?")
            params.append(permalink)
        if verification_method is not None:
            updates.append("verification_method = ?")
            params.append(verification_method)
        if error_code is not None:
            updates.append("error_code = ?")
            params.append(error_code)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if diagnostic_paths is not None:
            updates.append("diagnostic_paths_json = ?")
            params.append(json.dumps(diagnostic_paths))
        if completed:
            updates.append("completed_at = ?")
            params.append(now)
            
        params.append(attempt_id)
        with self._connection() as connection:
            connection.execute(
                f"UPDATE facebook_publication_attempts SET {', '.join(updates)} WHERE attempt_id = ?",
                tuple(params)
            )
            connection.commit()

    def get_latest_publication_attempt(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM facebook_publication_attempts WHERE job_id = ? ORDER BY started_at DESC LIMIT 1",
                (job_id,)
            ).fetchone()
        if not row:
            return None
        return dict(row)
        
    def get_publication_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM facebook_publication_attempts WHERE attempt_id = ?",
                (attempt_id,)
            ).fetchone()
        if not row:
            return None
        return dict(row)

SQLiteJobRepository = JobRepository
