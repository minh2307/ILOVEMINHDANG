from typing import Any
from app.application.ports.browser_port import BrowserPort
from app.infrastructure.browser.cdp_connection import CdpConnection

class PlaywrightBrowserAdapter(BrowserPort):
    def __init__(self, cdp_connection: CdpConnection):
        self._cdp_connection = cdp_connection
        self._context = None

    async def connect(self) -> None:
        self._context = await self._cdp_connection.connect()

    async def get_page(self, page_name: str) -> Any:
        if not self._context:
            await self.connect()
        page = await self._context.new_page()
        return page

    async def is_connected(self) -> bool:
        return self._context is not None
