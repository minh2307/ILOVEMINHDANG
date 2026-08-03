from __future__ import annotations

from typing import Any

from app.infrastructure.legacy.facebook_integrations import selectors


class FacebookPostService:
    async def create(self, page: Any, target_url: str, text: str, image_paths: list[str] | None = None) -> dict:
        await page.goto(target_url, wait_until="domcontentloaded")
        await page.locator(selectors.COMPOSER_ENTRY).first.click()
        composer = page.locator(selectors.COMPOSER_TEXT).first
        await composer.wait_for(state="visible")
        await composer.fill(text)
        if image_paths:
            upload = page.locator('input[type="file"]').first
            await upload.set_input_files(image_paths)
        await page.locator(selectors.PUBLISH_BUTTON).last.click()
        return {"submitted": True, "url": str(page.url)}

    async def share(self, page: Any, post_url: str, text: str = "") -> dict:
        await page.goto(post_url, wait_until="domcontentloaded")
        share = page.get_by_role("button", name="Share").or_(page.get_by_role("button", name="Chia sẻ"))
        await share.first.click()
        if text:
            await page.locator(selectors.COMPOSER_TEXT).last.fill(text)
        await page.locator(selectors.PUBLISH_BUTTON).last.click()
        return {"submitted": True, "url": str(page.url)}
