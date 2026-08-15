from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Optional

from app.application.ports.job_queue_port import JobQueuePort
from app.domain.enums.job_status import JobStatus, JobStatus as WorkflowStatus
from app.domain.models.facebook_job import FacebookJob


class SQLiteJobQueue(JobQueuePort):
    """Durable single-row-per-job queue with atomic claims and audit events."""

    INTERRUPTED_STATES = ("RUNNING", "ACQUIRING_BROWSER_LOCK", "WAITING_FOR_BROWSER_LOCK")

    def __init__(
        self,
        db_path: str,
        *,
        claim_eligibility: Callable[[dict[str, Any]], bool] | None = None,
    ):
        self.db_path = str(db_path)
        self._claim_eligibility = claim_eligibility
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS queue (
                    job_id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 10,
                    next_retry_at REAL NOT NULL DEFAULT 0.0,
                    error_message TEXT,
                    claimed_by TEXT,
                    lease_expires_at REAL,
                    last_heartbeat TEXT,
                    current_stage TEXT,
                    completed_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(queue)")}
            migrations = {
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "max_attempts": "INTEGER NOT NULL DEFAULT 10",
                "next_retry_at": "REAL NOT NULL DEFAULT 0.0",
                "error_message": "TEXT",
                "claimed_by": "TEXT",
                "lease_expires_at": "REAL",
                "last_heartbeat": "TEXT",
                "current_stage": "TEXT",
                "completed_at": "TEXT",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            }
            for name, definition in migrations.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE queue ADD COLUMN {name} {definition}")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS queue_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    timestamp TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_ready ON queue(status, next_retry_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_events_job ON queue_events(job_id, event_id)")

    @staticmethod
    def _safe_details(details: dict[str, Any] | None) -> str:
        blocked = {"password", "cookie", "cookies", "access_token", "token", "email", "phone"}
        clean = {
            str(key): "[REDACTED]" if str(key).lower() in blocked else value
            for key, value in (details or {}).items()
        }
        return json.dumps(clean, ensure_ascii=False, default=str)

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        event_type: str,
        *,
        from_state: str | None = None,
        to_state: str | None = None,
        attempt: int = 0,
        details: dict[str, Any] | None = None,
    ) -> None:
        conn.execute("""
            INSERT INTO queue_events
                (job_id, event_type, from_state, to_state, timestamp, attempt, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (job_id, event_type, from_state, to_state, self._timestamp(), attempt,
              self._safe_details(details)))

    async def enqueue(self, job: FacebookJob) -> bool:
        """Insert only once; never resurrect or overwrite an existing job id."""
        now = self._timestamp()
        with self._connect() as conn:
            cursor = conn.execute("""
                INSERT INTO queue
                    (job_id, job_type, payload, status, attempt_count, max_attempts,
                     next_retry_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO NOTHING
            """, (job.job_id, job.job_type.value, json.dumps(job.payload, ensure_ascii=False),
                  job.status.value, job.attempt_count, job.max_attempts, job.next_retry_at, now, now))
            inserted = cursor.rowcount == 1
            if inserted:
                self._insert_event(
                    conn, job.job_id, "JOB_STATE_CHANGED", to_state=job.status.value,
                    attempt=job.attempt_count, details={"reason": "enqueued"},
                )
            return inserted

    async def dequeue(
        self, *, worker_id: str = "legacy-worker", lease_seconds: float = 120.0
    ) -> Optional[FacebookJob]:
        worker_id = worker_id.strip() or f"worker-{uuid.uuid4().hex}"
        lease_seconds = max(1.0, float(lease_seconds))
        now_epoch = time.time()
        now = self._timestamp()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("""
                SELECT job_id, job_type, payload, status, attempt_count, max_attempts,
                       next_retry_at
                FROM queue
                WHERE status IN ('CREATED', 'PENDING', 'DOWNLOADREEL_RUNNING', 'FACEBOOK_PREPARING', 'RETRYABLE') AND next_retry_at <= ?
                ORDER BY next_retry_at, rowid
            """, (now_epoch,)).fetchall()
            row = None
            for candidate in rows:
                payload = json.loads(candidate["payload"])
                if self._claim_eligibility is None or self._claim_eligibility(payload):
                    row = candidate
                    break
            if row is None:
                return None
            changed = conn.execute("""
                UPDATE queue
                SET status = 'ACQUIRING_BROWSER_LOCK', claimed_by = ?,
                    lease_expires_at = ?, last_heartbeat = ?,
                    current_stage = 'ACQUIRING_BROWSER_LOCK', updated_at = ?
                WHERE job_id = ? AND status = ?
            """, (
                worker_id, now_epoch + lease_seconds, now, now,
                row["job_id"], row["status"],
            )).rowcount
            if changed != 1:
                return None
            self._insert_event(
                conn, row["job_id"], "JOB_STATE_CHANGED",
                from_state=row["status"], to_state="ACQUIRING_BROWSER_LOCK",
                attempt=row["attempt_count"], details={"claimed_by": worker_id},
            )
            from app.domain.enums.facebook_job_type import FacebookJobType
            return FacebookJob(
                job_id=row["job_id"], job_type=FacebookJobType(row["job_type"]),
                payload=json.loads(row["payload"]),
                status=WorkflowStatus.ACQUIRING_BROWSER_LOCK,
                attempt_count=row["attempt_count"], max_attempts=row["max_attempts"],
                next_retry_at=row["next_retry_at"],
            )

    async def heartbeat(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 120.0,
        current_stage: str | None = None,
    ) -> bool:
        """Extend a claim only when it is still owned by this worker."""
        now_epoch = time.time()
        now = self._timestamp()
        with self._connect() as conn:
            changed = conn.execute("""
                UPDATE queue
                SET lease_expires_at = ?, last_heartbeat = ?,
                    current_stage = COALESCE(?, current_stage), updated_at = ?
                WHERE job_id = ? AND claimed_by = ?
                  AND status IN ('ACQUIRING_BROWSER_LOCK', 'WAITING_FOR_BROWSER_LOCK', 'RUNNING')
            """, (
                now_epoch + max(0.01, float(lease_seconds)),
                now,
                current_stage,
                now,
                job_id,
                worker_id,
            )).rowcount
            return changed == 1

    async def set_state(
        self,
        job_id: str,
        state: JobStatus,
        *,
        event_type: str = "JOB_STATE_CHANGED",
        details: dict[str, Any] | None = None,
    ) -> bool:
        state_value = state.value
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, attempt_count FROM queue WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "UPDATE queue SET status = ?, current_stage = ?, updated_at = ? "
                "WHERE job_id = ?",
                (state_value, state_value, self._timestamp(), job_id),
            )
            self._insert_event(
                conn, job_id, event_type, from_state=row["status"], to_state=state_value,
                attempt=row["attempt_count"], details=details,
            )
            return True

    async def record_event(
        self, job_id: str, event_type: str, *, details: dict[str, Any] | None = None
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, attempt_count FROM queue WHERE job_id = ?", (job_id,)
            ).fetchone()
            self._insert_event(
                conn, job_id, event_type,
                from_state=row["status"] if row else None,
                to_state=row["status"] if row else None,
                attempt=row["attempt_count"] if row else 0,
                details=details,
            )

    async def retry(self, job_id: str, error: str, delay_seconds: float) -> bool:
        now = self._timestamp()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, attempt_count, max_attempts FROM queue WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return False
            attempt = row["attempt_count"] + 1
            if attempt > row["max_attempts"]:
                conn.execute(
                    "UPDATE queue SET status='BLOCKED', attempt_count=?, error_message=?, "
                    "claimed_by=NULL, lease_expires_at=NULL, last_heartbeat=NULL, updated_at=? "
                    "WHERE job_id=?",
                    (attempt, error, now, job_id),
                )
                self._insert_event(
                    conn, job_id, "JOB_STATE_CHANGED", from_state=row["status"],
                    to_state="BLOCKED", attempt=attempt,
                    details={"reason": "maximum_attempts_exceeded", "error": error},
                )
                return False
            next_retry_at = time.time() + max(0.0, delay_seconds)
            conn.execute("""
                UPDATE queue SET status='RETRYABLE', attempt_count=?, next_retry_at=?,
                       error_message=?, claimed_by=NULL, lease_expires_at=NULL,
                       last_heartbeat=NULL, updated_at=? WHERE job_id=?
            """, (attempt, next_retry_at, error, now, job_id))
            self._insert_event(
                conn, job_id, "PLAYWRIGHT_RETRY_SCHEDULED",
                from_state=row["status"], to_state="RETRYABLE", attempt=attempt,
                details={"delay_seconds": delay_seconds, "error": error},
            )
            return True

    async def recover_jobs(self) -> int:
        """Recover only claims whose lease has expired.

        Rows created by older releases have no lease metadata; those interrupted
        rows are treated as stale so upgrades remain recoverable.
        """
        now = self._timestamp()
        now_epoch = time.time()
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in self.INTERRUPTED_STATES)
            rows = conn.execute(
                f"SELECT job_id, status, attempt_count, max_attempts FROM queue "
                f"WHERE status IN ({placeholders}) "
                "AND (lease_expires_at IS NULL OR lease_expires_at <= ?)",
                (*self.INTERRUPTED_STATES, now_epoch),
            ).fetchall()
            for row in rows:
                attempt = row["attempt_count"] + 1
                exhausted = attempt > row["max_attempts"]
                target = "BLOCKED" if exhausted else "RETRYABLE"
                error_message = (
                    "Maximum retry attempts exceeded during stale lease recovery"
                    if exhausted
                    else "Worker lease expired before completion"
                )
                conn.execute("""
                    UPDATE queue SET status=?, attempt_count=?, next_retry_at=0,
                           error_message=?,
                           claimed_by=NULL, lease_expires_at=NULL, last_heartbeat=NULL,
                           current_stage=?, updated_at=?
                    WHERE job_id=?
                """, (
                    target,
                    attempt,
                    error_message,
                    target,
                    now,
                    row["job_id"],
                ))
                self._insert_event(
                    conn,
                    row["job_id"],
                    "MAXIMUM_ATTEMPTS_EXCEEDED"
                    if exhausted
                    else "PLAYWRIGHT_RETRY_SCHEDULED",
                    from_state=row["status"],
                    to_state=target,
                    attempt=attempt,
                    details={
                        "reason": "maximum_attempts_exceeded"
                        if exhausted
                        else "expired_worker_lease",
                        "delay_seconds": 0,
                    },
                )
            return len(rows)

    async def complete(self, job_id: str) -> None:
        await self.set_state(
            job_id, JobStatus.COMPLETED, details={"reason": "dispatch_completed"}
        )
        now = self._timestamp()
        with self._connect() as conn:
            conn.execute(
                "UPDATE queue SET claimed_by=NULL, lease_expires_at=NULL, "
                "last_heartbeat=NULL, completed_at=?, updated_at=? WHERE job_id=?",
                (now, now, job_id),
            )

    async def fail(self, job_id: str, error: str) -> None:
        await self.set_state(
            job_id, JobStatus.FAILED,
            details={"error": error, "reason": "non_retryable_error"}
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE queue SET error_message = ?, claimed_by=NULL, lease_expires_at=NULL, "
                "last_heartbeat=NULL, updated_at = ? WHERE job_id = ?",
                (error, self._timestamp(), job_id),
            )

    async def get_record(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM queue WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    async def list_records(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM queue ORDER BY rowid")]

    async def list_events(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM queue_events WHERE job_id = ? ORDER BY event_id", (job_id,)
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            events.append(item)
        return events
