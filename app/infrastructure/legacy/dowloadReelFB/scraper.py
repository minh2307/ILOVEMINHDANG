from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.browser.facebook_job import FacebookJobType
from app.browser.facebook_job_client import FacebookJobClient


class ReelScraper:
    """Synchronous producer used by the legacy downloader; browser work stays in the worker."""

    def __init__(self, client: FacebookJobClient | None = None):
        self.client = client or FacebookJobClient()

    def scrape(self, url: str) -> dict:
        metadata = self.client.execute(
            FacebookJobType.EXTRACT_REEL_METADATA,
            {"url": url},
            idempotency_key=f"reel-metadata:{url}",
        )
        comments = self.client.execute(
            FacebookJobType.EXTRACT_COMMENTS,
            {"url": url, "limit": 100},
            idempotency_key=f"reel-comments:{url}",
        )
        return {
            "caption": str(metadata.get("caption") or ""),
            "comments": list(comments or []),
            "commentDetails": [{"content": value} for value in comments or []],
            "postLiked": False,
            "likedComments": 0,
            "skippedAuthorComments": 0,
            "skippedSpamComments": 0,
            "interactionDuration": 0.0,
            "activityLimitReached": False,
        }
