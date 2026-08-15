#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain.policies.external_side_effect_policy import (
    FacebookSubmissionEvidence,
    build_facebook_submission_evidence,
)


MANUAL_STATUS = "POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW"
ACTIVE_QUEUE_STATES = (
    "CREATED",
    "PENDING",
    "RETRYABLE",
    "RUNNING",
    "ACQUIRING_BROWSER_LOCK",
    "WAITING_FOR_BROWSER_LOCK",
    "FACEBOOK_PREPARING",
)


def expected_confirmation(job_id: str, fingerprint: str) -> str:
    return f"QUARANTINE-FACEBOOK-DUPLICATE:{job_id}:{fingerprint}"


def _evidence(connection: sqlite3.Connection, job_id: str) -> FacebookSubmissionEvidence:
    connection.row_factory = sqlite3.Row
    job = connection.execute(
        "SELECT data_json FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if job is None:
        raise LookupError(f"Job not found: {job_id}")
    events = [
        {
            "event_type": row["event_type"],
            "details": json.loads(row["details_json"] or "{}"),
            "created_at": row["created_at"],
        }
        for row in connection.execute(
            "SELECT event_type, details_json, created_at FROM job_events "
            "WHERE job_id = ? ORDER BY event_id",
            (job_id,),
        )
    ]
    attempts = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM facebook_publication_attempts "
            "WHERE job_id = ? ORDER BY started_at, attempt_id",
            (job_id,),
        )
    ]
    return build_facebook_submission_evidence(
        json.loads(job["data_json"] or "{}"),
        events=events,
        publication_attempts=attempts,
    )


def _backup(database: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = database.with_name(
        f"{database.stem}_backup_{timestamp}{database.suffix}"
    )
    source_connection = sqlite3.connect(
        f"file:{database.resolve()}?mode=ro", uri=True, timeout=30
    )
    backup_connection = sqlite3.connect(destination, timeout=30)
    try:
        source_connection.backup(backup_connection)
    finally:
        backup_connection.close()
        source_connection.close()
    with sqlite3.connect(
        f"file:{destination.resolve()}?mode=ro", uri=True
    ) as check:
        result = check.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"Backup integrity check failed: {result}")
    return destination


def quarantine(
    database: Path,
    *,
    job_id: str,
    fingerprint: str,
    apply: bool,
    confirmation: str | None = None,
) -> dict[str, Any]:
    database = database.expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(database)
    uri = f"file:{database}?mode={'rw' if apply else 'ro'}"
    with sqlite3.connect(uri, uri=True, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        if not apply:
            connection.execute("PRAGMA query_only = ON")
        job = connection.execute(
            "SELECT job_id, status, data_json, attempt_count, max_attempts, "
            "updated_at FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        evidence = _evidence(connection, job_id)
        active_queue = [
            dict(row)
            for row in connection.execute(
                "SELECT job_id, status, attempt_count, max_attempts FROM queue "
                "WHERE payload LIKE ? AND status IN "
                f"({','.join('?' for _ in ACTIVE_QUEUE_STATES)})",
                (f'%"workflow_job_id": "{job_id}"%', *ACTIVE_QUEUE_STATES),
            )
        ]
        report: dict[str, Any] = {
            "mode": "apply" if apply else "dry-run",
            "database": str(database),
            "job_id": job_id,
            "current_status": job["status"],
            "target_status": MANUAL_STATUS,
            "fingerprint": evidence.content_fingerprint,
            "target_url": evidence.target_url,
            "submitted_at": evidence.submitted_at,
            "publish_attempts": evidence.publish_attempts,
            "possible_duplicate": evidence.possible_duplicate,
            "active_queue_rows": active_queue,
            "backup_path": None,
            "changed_job_count": 0,
            "blocked_queue_count": 0,
        }
        if evidence.content_fingerprint != fingerprint:
            raise ValueError("Fingerprint mismatch; refusing to quarantine")
        if not evidence.possible_duplicate:
            raise ValueError("Fewer than two durable submit attempts; refusing quarantine")
        if not apply:
            return report
        if confirmation != expected_confirmation(job_id, fingerprint):
            raise ValueError("Exact scoped quarantine confirmation is required")

    backup_path = _backup(database)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(f"file:{database}?mode=rw", uri=True, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("BEGIN IMMEDIATE")
        evidence = _evidence(connection, job_id)
        if evidence.content_fingerprint != fingerprint or not evidence.possible_duplicate:
            connection.rollback()
            raise RuntimeError("Evidence changed after backup; refusing quarantine")
        job = connection.execute(
            "SELECT status, data_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        payload = json.loads(job["data_json"] or "{}")
        payload.update(
            {
                "facebook_publication_state": MANUAL_STATUS,
                "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
                "facebook_submitted_at": evidence.submitted_at,
                "facebook_content_hash": evidence.content_fingerprint,
                "facebook_target_url": evidence.target_url,
                "facebook_possible_duplicate_reason": (
                    "multiple durable submit attempts detected during incident recovery"
                ),
                "facebook_automation_blocked": True,
            }
        )
        changed = connection.execute(
            "UPDATE jobs SET previous_status = status, status = ?, data_json = ?, "
            "output_payload_json = ?, error_code = NULL, error_message = NULL, "
            "completed_at = NULL, updated_at = ? WHERE job_id = ?",
            (
                MANUAL_STATUS,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                now,
                job_id,
            ),
        ).rowcount
        connection.execute(
            "INSERT INTO job_events(job_id, from_status, to_status, details_json, "
            "created_at, event_type, attempt) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                job_id,
                job["status"],
                MANUAL_STATUS,
                json.dumps(
                    {
                        "reason": "scoped_incident_recovery",
                        "publish_attempts": evidence.publish_attempts,
                        "submitted_at": evidence.submitted_at,
                        "content_fingerprint": evidence.content_fingerprint,
                        "target_url": evidence.target_url,
                    },
                    ensure_ascii=False,
                ),
                now,
                MANUAL_STATUS,
            ),
        )
        queue_rows = connection.execute(
            "SELECT job_id, status, attempt_count FROM queue WHERE payload LIKE ? "
            "AND status IN "
            f"({','.join('?' for _ in ACTIVE_QUEUE_STATES)})",
            (f'%"workflow_job_id": "{job_id}"%', *ACTIVE_QUEUE_STATES),
        ).fetchall()
        for queue_row in queue_rows:
            connection.execute(
                "UPDATE queue SET status = 'BLOCKED', current_stage = 'BLOCKED', "
                "claimed_by = NULL, lease_expires_at = NULL, error_message = ?, "
                "updated_at = ? WHERE job_id = ?",
                (MANUAL_STATUS, now, queue_row["job_id"]),
            )
            connection.execute(
                "INSERT INTO queue_events(job_id, event_type, from_state, to_state, "
                "timestamp, attempt, details_json) VALUES (?, ?, ?, 'BLOCKED', ?, ?, ?)",
                (
                    queue_row["job_id"],
                    MANUAL_STATUS,
                    queue_row["status"],
                    now,
                    int(queue_row["attempt_count"] or 0),
                    json.dumps({"workflow_job_id": job_id}),
                ),
            )
        connection.commit()
    report.update(
        {
            "backup_path": str(backup_path),
            "changed_job_count": changed,
            "blocked_queue_count": len(queue_rows),
        }
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run-first quarantine for one evidenced Facebook duplicate job"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation")
    args = parser.parse_args()
    print(
        json.dumps(
            quarantine(
                args.database,
                job_id=args.job_id,
                fingerprint=args.fingerprint,
                apply=args.apply,
                confirmation=args.confirmation,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
