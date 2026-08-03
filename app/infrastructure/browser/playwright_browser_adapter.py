from typing import Any
from app.application.ports.browser_port import BrowserPort
from app.domain.models.browser_health import BrowserHealth
from app.errors import BrowserPageOwnershipError
from app.infrastructure.browser.cdp_connection import CdpConnection

class PlaywrightBrowserAdapter(BrowserPort):
    def __init__(self, cdp_connection: CdpConnection):
        self._cdp_connection = cdp_connection
        self._context = None
        self._owned_pages: set[int] = set()

    async def connect(self) -> None:
        self._context = await self._cdp_connection.connect()

    async def get_page(self, page_name: str) -> Any:
        if not self._context:
            await self.connect()
        page = await self._context.new_page()
        return page

    async def is_connected(self) -> bool:
        return self._context is not None

    async def acquire_page(self, purpose: str) -> Any:
        page = await self.get_page(purpose)
        self._owned_pages.add(id(page))
        return page

    async def release_page(self, page: Any) -> bool:
        if id(page) not in self._owned_pages:
            raise BrowserPageOwnershipError(
                "Refusing to close an unowned page",
                phase="BROWSER_SESSION",
                operation="release_page",
            )
        self._owned_pages.discard(id(page))
        if page.is_closed():
            return False
        await page.close()
        return True

    async def ensure_connected(self, page: Any | None = None) -> None:
        if not await self.is_connected():
            await self.connect()

    async def get_health(self, page: Any | None = None) -> BrowserHealth:
        from app.domain.models.browser_health import BrowserHealthState

        if self._context is None:
            return BrowserHealth(
                BrowserHealthState.DISCONNECTED, False, False
            )
        if page is not None and page.is_closed():
            return BrowserHealth(
                BrowserHealthState.PAGE_CLOSED, True, True, False
            )
        return BrowserHealth(
            BrowserHealthState.CONNECTED,
            True,
            True,
            None if page is None else True,
        )
