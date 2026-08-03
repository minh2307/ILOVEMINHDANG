#!/usr/bin/env python3
"""Plan or apply a non-destructive migration from the retired queue database."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.use_cases.create_job_use_case import CreateJobUseCase
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.config.settings import Settings
from app.infrastructure.persistence.sqlite_job_queue import SQLiteJobQueue
from app.infrastructure.persistence.sqlite_job_repository import JobRepository


def read_legacy_rows(source: Path) -> list[dict[str, Any]]:
    if not source.is_file():
        raise FileNotFoundError(f"Legacy queue not found: {source}")
    connection = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "queue" not in tables:
            raise ValueError(f"Legacy database has no queue table: {source}")
        return [dict(row) for row in connection.execute("SELECT * FROM queue ORDER BY rowid")]
    finally:
        connection.close()


async def migrate(
    source: Path, target: Path, *, apply: bool = False
) -> list[dict[str, Any]]:
    rows = read_legacy_rows(source)
    repository: JobRepository | None = None
    scheduler: ScheduleWorkflowJobsUseCase | None = None
    creator: CreateJobUseCase | None = None
    if apply:
        repository = JobRepository(target)
        repository.initialize()
        queue = SQLiteJobQueue(str(target))
        scheduler = ScheduleWorkflowJobsUseCase(repository, queue)
        creator = CreateJobUseCase(repository, scheduler)
    report: list[dict[str, Any]] = []

    for row in rows:
        status = str(row.get("status") or "")
        job_type = str(row.get("job_type") or "")
        if status in {"COMPLETED", "FAILED", "BLOCKED", "CANCELLED"}:
            report.append({"legacy_job_id": row.get("job_id"), "action": "skip_terminal"})
            continue
        try:
            payload = json.loads(row.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            report.append({"legacy_job_id": row.get("job_id"), "action": "invalid_payload"})
            continue

        if job_type == "DOWNLOAD_REEL" and payload.get("url"):
            item = {
                "legacy_job_id": row.get("job_id"),
                "action": "create_or_reuse_workflow",
                "source_url": payload["url"],
                "applied": apply,
            }
            if apply:
                assert creator is not None
                result = await creator.execute(str(payload["url"]))
                item.update({"workflow_job_id": result.job_id, **result.data})
            report.append(item)
            continue

        if job_type == "CREATE_POST":
            workflow = (
                repository.get_job(str(row.get("job_id") or ""))
                if repository is not None else None
            )
            report.append({
                "legacy_job_id": row.get("job_id"),
                "action": "use_workflow_state" if workflow else "manual_mapping_required",
                "workflow_status": workflow.status.value if workflow else None,
                "note": "Never migrates an old CREATE_POST row as publish confirmation.",
            })
            if apply and workflow and scheduler and workflow.status in scheduler.ELIGIBLE:
                await scheduler.schedule_job(workflow.job_id)
            continue

        report.append({
            "legacy_job_id": row.get("job_id"),
            "action": "unsupported_legacy_type",
            "job_type": job_type,
        })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("runtime/queue.db"))
    parser.add_argument("--target", type=Path)
    parser.add_argument(
        "--apply", action="store_true",
        help="Write idempotent workflow/queue rows. The legacy database is never changed.",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    target = (args.target or settings.database_path).resolve()
    try:
        report = asyncio.run(migrate(args.source.resolve(), target, apply=args.apply))
        print(json.dumps({
            "mode": "apply" if args.apply else "dry-run",
            "source": str(args.source.resolve()),
            "target": str(target),
            "items": report,
        }, ensure_ascii=False, indent=2))
        return 0
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
