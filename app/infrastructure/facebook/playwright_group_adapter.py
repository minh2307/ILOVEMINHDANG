from __future__ import annotations

from typing import Any

from app.application.ports.browser_port import BrowserPort
from app.application.ports.facebook_group_port import FacebookGroupPort


class PlaywrightGroupAdapter(FacebookGroupPort):
    """Inactive compatibility seam; group automation has no verified workflow."""

    def __init__(self, browser: BrowserPort) -> None:
        self._browser = browser

    async def join_group(self, group_url: str) -> dict[str, Any]:
        raise RuntimeError(
            "Group joining is disabled until Facebook membership state can be verified."
        )

    async def publish_to_group(self, group_url: str, content: str) -> dict[str, Any]:
        raise RuntimeError(
            "Group publishing is disabled until publication can be verified."
        )
