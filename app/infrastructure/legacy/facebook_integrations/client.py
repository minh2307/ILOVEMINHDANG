from __future__ import annotations

from typing import Any

from app.browser.facebook_job import FacebookJob, FacebookJobType
from app.infrastructure.legacy.facebook_integrations import selectors
from app.infrastructure.legacy.facebook_integrations.comment_service import FacebookCommentService
from app.infrastructure.legacy.facebook_integrations.group_service import FacebookGroupService
from app.infrastructure.legacy.facebook_integrations.post_service import FacebookPostService
from app.infrastructure.legacy.facebook_integrations.reel_service import FacebookReelService


class FacebookAutomationClient:
    def __init__(self) -> None:
        self.reels = FacebookReelService()
        self.posts = FacebookPostService()
        self.comments = FacebookCommentService()
        self.groups = FacebookGroupService()

    async def handle(self, job: FacebookJob, page: Any) -> Any:
        payload = job.payload
        if job.job_type is FacebookJobType.CHECK_LOGIN:
            await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            return {"logged_in": await page.locator(selectors.LOGIN_INPUTS).count() == 0, "url": str(page.url)}
        if job.job_type is FacebookJobType.EXTRACT_REEL_METADATA:
            return await self.reels.extract_metadata(page, str(payload["url"]))
        if job.job_type is FacebookJobType.EXTRACT_COMMENTS:
            return await self.reels.extract_comments(page, str(payload["url"]), int(payload.get("limit", 100)))
        if job.job_type is FacebookJobType.CREATE_POST:
            return await self.posts.create(page, str(payload["target_url"]), str(payload["text"]), list(payload.get("image_paths") or []))
        if job.job_type is FacebookJobType.SHARE_POST:
            return await self.posts.share(page, str(payload["post_url"]), str(payload.get("text") or ""))
        if job.job_type is FacebookJobType.COMMENT_POST:
            return await self.comments.comment(page, str(payload["post_url"]), str(payload["text"]))
        if job.job_type is FacebookJobType.JOIN_GROUP:
            return await self.groups.join(page, str(payload["group_url"]))
        if job.job_type is FacebookJobType.COLLECT_PAGE_POSTS:
            await page.goto(str(payload["page_url"]), wait_until="domcontentloaded")
            posts = page.locator(selectors.PAGE_POSTS)
            result = []
            for index in range(min(await posts.count(), int(payload.get("max_posts", 100)))):
                item = posts.nth(index)
                result.append({"message": (await item.inner_text()).strip()})
            return result
        raise ValueError(f"Unsupported Facebook job type: {job.job_type.value}")

    def register(self, worker: Any) -> None:
        for job_type in FacebookJobType:
            if job_type is not FacebookJobType.DOWNLOAD_REEL:
                worker.register_handler(job_type, self.handle)
