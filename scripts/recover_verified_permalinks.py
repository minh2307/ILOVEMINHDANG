from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.browser.facebook_client import FacebookWebClient
from app.domain.enums.job_status import JobStatus


def _eligible_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT job_id, status, data_json FROM jobs WHERE status = ? ORDER BY job_id",
        (JobStatus.POST_URL_EXTRACTION_FAILED.value,),
    ).fetchall()
    eligible: list[dict[str, Any]] = []
    for job_id, status, raw_data in rows:
        try:
            data = json.loads(raw_data or "{}")
            verified = bool(data.get("facebook_publication_verified")) or str(
                data.get("facebook_submission_status") or ""
            ).upper() == "RECONCILED_VERIFIED"
            raw_url = str(
                data.get("facebook_post_url")
                or data.get("facebook_post_url_candidate")
                or ""
            ).strip()
            if not verified or not raw_url:
                continue
            canonical = FacebookWebClient.normalize_permalink(
                raw_url, base_url=raw_url
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        eligible.append(
            {
                "job_id": job_id,
                "from_status": status,
                "to_status": JobStatus.POST_URL_EXTRACTED.value,
                "canonical_permalink": canonical,
                "post_id": FacebookWebClient.extract_post_id(canonical),
            }
        )
    return eligible


def _backup_database(database_path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_path = database_path.with_name(
        f"{database_path.stem}_backup_verified_permalink_{stamp}{database_path.suffix}"
    )
    source = sqlite3.connect(database_path)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    if not backup_path.is_file() or backup_path.stat().st_size <= 0:
        raise RuntimeError("Verified-permalink migration backup failed")
    return backup_path


def run_recovery(database_path: Path, *, apply: bool = False) -> dict[str, Any]:
    database_path = Path(database_path).expanduser().resolve(strict=True)
    uri = f"file:{database_path}?mode={'rw' if apply else 'ro'}"
    with sqlite3.connect(uri, uri=True) as connection:
        eligible = _eligible_rows(connection)
    report: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "database": str(database_path),
        "eligible": eligible,
        "changed_count": 0,
        "backup_path": None,
    }
    if not apply or not eligible:
        return report

    backup_path = _backup_database(database_path)
    now = datetime.now(UTC).isoformat()
    changed = 0
    with sqlite3.connect(database_path, timeout=30) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        currently_eligible = {
            row["job_id"]: row for row in _eligible_rows(connection)
        }
        for planned in eligible:
            job_id = planned["job_id"]
            current = currently_eligible.get(job_id)
            if current != planned:
                continue
            raw = connection.execute(
                "SELECT data_json FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            data = json.loads(raw[0] or "{}")
            data.update(
                {
                    "facebook_post_url": planned["canonical_permalink"],
                    "facebook_post_id": planned["post_id"]
                    or data.get("facebook_post_id"),
                    "facebook_permalink_extraction_method": "migration_persisted_verified_permalink",
                    "facebook_permalink_error": None,
                }
            )
            encoded = json.dumps(data, ensure_ascii=False)
            cursor = connection.execute(
                "UPDATE jobs SET previous_status=status, status=?, data_json=?, "
                "output_payload_json=?, error_code=NULL, error_message=NULL, updated_at=? "
                "WHERE job_id=? AND status=?",
                (
                    JobStatus.POST_URL_EXTRACTED.value,
                    encoded,
                    encoded,
                    now,
                    job_id,
                    JobStatus.POST_URL_EXTRACTION_FAILED.value,
                ),
            )
            if cursor.rowcount != 1:
                continue
            connection.execute(
                "INSERT INTO job_events(job_id,from_status,to_status,details_json,created_at,event_type,attempt) "
                "VALUES(?,?,?,?,?,'VERIFIED_PERMALINK_RECOVERED',0)",
                (
                    job_id,
                    JobStatus.POST_URL_EXTRACTION_FAILED.value,
                    JobStatus.POST_URL_EXTRACTED.value,
                    json.dumps(
                        {"method": "persisted_verified_permalink"},
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            changed += 1
        connection.commit()
    report.update(
        {"changed_count": changed, "backup_path": str(backup_path)}
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover failed extraction rows that already have a verified permalink."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply eligible changes after creating a new SQLite backup (default: dry-run).",
    )
    args = parser.parse_args()
    print(json.dumps(run_recovery(args.database, apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
