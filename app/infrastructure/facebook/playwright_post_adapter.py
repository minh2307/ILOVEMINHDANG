from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.ports.browser_port import BrowserPort
from app.application.ports.facebook_post_port import FacebookPostPort


class PlaywrightPostAdapter(FacebookPostPort):
    """Inactive compatibility adapter.

    The previous implementation silently constructed a second browser stack and
    auto-confirmed publication. That behavior is intentionally disabled; all
    post actions must pass through ProcessJobUseCase and its explicit gates.
    """

    def __init__(self, browser: BrowserPort) -> None:
        self._browser = browser

    async def create_post(
        self,
        content: str,
        images: list[Path] | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        raise RuntimeError(
            "Standalone post creation is disabled. Use `python main.py resume "
            "--job-id <id>` and `confirm-publish` at the manual gate."
        )

    async def share_post(self, post_url: str) -> dict[str, Any]:
        raise RuntimeError(
            "Standalone share verification is not implemented by the verified "
            "workflow and cannot report success safely."
        )
