# -*- coding: utf-8 -*-
"""Playwright worker client for browser-based Page collection."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.browser.facebook_job import FacebookJobType
from app.browser.facebook_job_client import FacebookJobClient


class BrowserCrawlError(RuntimeError):
    pass


class BrowserClient:
    def __init__(self, page_url: str, max_posts: int = 100, **_: object):
        self.page_url = page_url
        self.max_posts = max_posts
        self.client = FacebookJobClient()

    def fetch_posts(self):
        try:
            return self.client.execute(
                FacebookJobType.COLLECT_PAGE_POSTS,
                {"page_url": self.page_url, "max_posts": self.max_posts},
                idempotency_key=f"collect-page:{self.page_url}:{self.max_posts}",
            )
        except Exception as exc:
            raise BrowserCrawlError(str(exc)) from exc
