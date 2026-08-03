from __future__ import annotations

from typing import Any

from app.infrastructure.legacy.facebook_integrations import selectors


class FacebookReelService:
    async def extract_metadata(self, page: Any, url: str) -> dict:
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        caption = ""
        locator = page.locator(selectors.REEL_CAPTION)
        if await locator.count():
            caption = (await locator.first.inner_text()).strip()
        return {"url": str(page.url), "caption": caption, "title": await page.title()}

    async def extract_comments(self, page: Any, url: str, limit: int = 100) -> list[str]:
        await page.goto(url, wait_until="domcontentloaded")
        locator = page.locator(selectors.COMMENT_ITEMS)
        await locator.first.wait_for(state="visible", timeout=10_000)
        values: list[str] = []
        for index in range(min(await locator.count(), max(1, limit))):
            text = (await locator.nth(index).inner_text()).strip()
            if text and text not in values:
                values.append(text)
        return values
