from __future__ import annotations

from typing import Any

from app.infrastructure.legacy.facebook_integrations import selectors


class FacebookCommentService:
    async def comment(self, page: Any, post_url: str, text: str) -> dict:
        await page.goto(post_url, wait_until="domcontentloaded")
        box = page.locator(selectors.COMMENT_INPUT).last
        await box.wait_for(state="visible")
        await box.fill(text)
        await box.press("Enter")
        return {"submitted": True, "url": str(page.url)}
