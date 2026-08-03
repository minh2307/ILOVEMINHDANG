from typing import Protocol, Any, Dict

class FacebookGroupPort(Protocol):
    async def join_group(self, group_url: str) -> Dict[str, Any]:
        ...

    async def publish_to_group(self, group_url: str, content: str) -> Dict[str, Any]:
        ...
