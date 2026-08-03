from __future__ import annotations

from typing import Any


class FacebookTabManager:
    """Owns logical Facebook tabs and never closes a tab owned by another job."""

    def __init__(self, context: Any):
        self.context = context
        self._tabs: dict[str, Any] = {}
        self._owners: dict[str, str] = {}

    @staticmethod
    def _alive(page: Any) -> bool:
        return page is not None and not page.is_closed()

    async def get(self, name: str, *, job_id: str, temporary: bool = True) -> Any:
        page = self._tabs.get(name)
        if self._alive(page):
            owner = self._owners.get(name)
            if owner not in (None, job_id):
                raise RuntimeError(f"Tab {name!r} belongs to job {owner}")
            self._owners[name] = job_id
            return page
        if name == "facebook_main":
            page = next(
                (item for item in self.context.pages if self._alive(item) and "facebook.com" in item.url),
                None,
            )
        if page is None or not self._alive(page):
            page = await self.context.new_page()
        self._tabs[name] = page
        self._owners[name] = job_id
        if name == "facebook_main" or not temporary:
            self._owners[name] = job_id
        return page

    async def release_job(self, job_id: str) -> None:
        for name, owner in list(self._owners.items()):
            if owner != job_id:
                continue
            page = self._tabs.get(name)
            if name != "facebook_main" and self._alive(page):
                await page.close()
                self._tabs.pop(name, None)
            self._owners.pop(name, None)

    async def ensure_main(self, *, job_id: str) -> Any:
        return await self.get("facebook_main", job_id=job_id, temporary=False)
