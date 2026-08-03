from typing import Protocol, Any

class BrowserPort(Protocol):
    async def connect(self) -> None:
        ...

    async def get_page(self, page_name: str) -> Any:
        ...

    async def is_connected(self) -> bool:
        ...
