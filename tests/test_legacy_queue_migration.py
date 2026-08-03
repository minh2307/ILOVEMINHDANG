from __future__ import annotations

import json
import sqlite3

import pytest

from app.infrastructure.persistence.sqlite_job_queue import SQLiteJobQueue
from app.infrastructure.persistence.sqlite_job_repository import JobRepository
from scripts.migrate_legacy_queue import migrate


def make_legacy_queue(path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE queue(job_id TEXT, job_type TEXT, payload TEXT, status TEXT)"
        )
        for job_id in ("old-1", "old-2"):
            connection.execute(
                "INSERT INTO queue VALUES (?, 'DOWNLOAD_REEL', ?, 'CREATED')",
                (
                    job_id,
                    json.dumps({"url": "https://facebook.com/reel/123"}),
                ),
            )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_legacy_queue_dry_run_does_not_create_target(tmp_path):
    source = tmp_path / "legacy.db"
    target = tmp_path / "target.db"
    make_legacy_queue(source)

    report = await migrate(source, target)

    assert not target.exists()
    assert [item["action"] for item in report] == [
        "create_or_reuse_workflow", "create_or_reuse_workflow",
    ]


@pytest.mark.asyncio
async def test_legacy_queue_apply_preserves_source_and_deduplicates(tmp_path):
    source = tmp_path / "legacy.db"
    target = tmp_path / "target.db"
    make_legacy_queue(source)
    source_size = source.stat().st_size

    report = await migrate(source, target, apply=True)

    assert source.exists() and source.stat().st_size == source_size
    repository = JobRepository(target)
    repository.initialize()
    assert len(repository.list_jobs()) == 1
    assert len(await SQLiteJobQueue(str(target)).list_records()) == 1
    assert report[1]["reused"] is True
