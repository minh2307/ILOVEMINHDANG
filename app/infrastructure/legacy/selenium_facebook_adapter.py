from typing import Any
from app.application.ports.browser_lock_port import BrowserLockPort

class LegacySeleniumFacebookAdapter:
    def __init__(self, browser_lock: BrowserLockPort):
        self._browser_lock = browser_lock
        self._driver = None

    async def connect(self):
        # TODO: Move all logic to Playwright. This is a temporary adapter.
        # Should not launch a new Chrome. Must attach to 127.0.0.1:9222.
        pass

    async def disconnect(self):
        # Should not call driver.quit() since it closes the shared browser.
        pass
