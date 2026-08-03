from __future__ import annotations

from typing import Any

from app.infrastructure.legacy.facebook_integrations import selectors


class FacebookGroupService:
    async def join(self, page: Any, group_url: str) -> dict:
        await page.goto(group_url, wait_until="domcontentloaded")
        button = page.locator(selectors.JOIN_BUTTON).first
        await button.wait_for(state="visible")
        await button.click()
        return {"submitted": True, "url": str(page.url)}
