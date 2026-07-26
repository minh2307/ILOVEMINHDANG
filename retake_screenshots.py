"""
Retake screenshots for an existing job without re-running the full pipeline.
Usage:  python retake_screenshots.py <job_id>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.config.settings import Settings
from app.repositories.job_repository import JobRepository
from app.browser.chrome_manager import ChromeManager
from app.browser.selector_resolver import SelectorResolver
from app.services.screenshot_service import ScreenshotService
from app.logging_setup import configure_logging


async def retake(job_id: str) -> None:
    settings = Settings.from_env()
    configure_logging(settings)
    repo = JobRepository(settings.database_path)
    repo.initialize()

    job = repo.get_job(job_id)
    if job is None:
        print(f"Job not found: {job_id}")
        sys.exit(1)

    view_url = job.data.get("cdha_view_url")
    if not view_url:
        print(f"No cdha_view_url in job data for {job_id}")
        sys.exit(1)

    job_dir = (settings.job_data_dir / job_id).resolve()
    resolver = SelectorResolver(settings.selectors_path)
    svc = ScreenshotService(resolver)

    async with ChromeManager(settings) as chrome:
        page = await chrome.new_page()
        print(f"Opening: {view_url}")
        await page.goto(view_url, wait_until="domcontentloaded",
                        timeout=settings.page_timeout_seconds * 1000)

        # Wait a bit for the full page to render
        await page.wait_for_timeout(2000)

        print("Taking screenshots...")
        paths, warnings = await svc.capture_required(page, job_dir)

    for p in paths:
        print(f"  ✓ {p}")
    for w in warnings:
        print(f"  ⚠ {w}")
    print("Done.")


if __name__ == "__main__":
    job_id = sys.argv[1] if len(sys.argv) > 1 else "cf768a9e6fe14c41ade548b077fecd75"
    asyncio.run(retake(job_id))
