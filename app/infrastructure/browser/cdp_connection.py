from __future__ import annotations

from typing import Any

from app.browser.facebook_browser_manager import FacebookBrowserManager
from app.config.facebook_browser import FacebookBrowserConfig
from app.config.settings import Settings


class CdpConnection:
    """Compatibility adapter that delegates all CDP ownership to the central manager."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        *,
        manager: FacebookBrowserManager | None = None,
        config: FacebookBrowserConfig | None = None,
    ) -> None:
        self._config = config or FacebookBrowserConfig.from_settings(Settings.from_env())
        if endpoint_url and endpoint_url.rstrip("/") != self._config.cdp_url.rstrip("/"):
            raise ValueError(
                f"CDP endpoint {endpoint_url} does not match central browser config "
                f"{self._config.cdp_url}"
            )
        self._manager = manager or FacebookBrowserManager(config=self._config)

    async def connect(self) -> Any:
        return await self._manager.start()

    async def close(self) -> None:
        await self._manager.close()
