from typing import Protocol, Any, Dict

class FacebookReelPort(Protocol):
    async def download_reel(self, url: str) -> Dict[str, Any]:
        ...

    async def extract_metadata(self, url: str) -> Dict[str, Any]:
        ...
