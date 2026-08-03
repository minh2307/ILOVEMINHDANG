from typing import Protocol, Any, Dict, List
from pathlib import Path

class FacebookPostPort(Protocol):
    async def create_post(self, content: str, images: List[Path] = None, job_id: str = None) -> Dict[str, Any]:
        ...

    async def share_post(self, post_url: str) -> Dict[str, Any]:
        ...
