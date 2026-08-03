from typing import Protocol, Any

from app.domain.models.browser_health import BrowserHealth

class BrowserPort(Protocol):
    async def acquire_page(self, purpose: str) -> Any:
        ...

    async def release_page(self, page: Any) -> bool:
        ...

    async def ensure_connected(self, page: Any | None = None) -> None:
        ...

    async def get_health(self, page: Any | None = None) -> BrowserHealth:
        ...


BrowserSessionPort = BrowserPort
