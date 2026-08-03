from __future__ import annotations

import asyncio
from typing import Any, Dict

import yt_dlp

from app.application.ports.browser_port import BrowserPort
from app.application.ports.facebook_reel_port import FacebookReelPort
from app.config.settings import Settings


class PlaywrightReelAdapter(FacebookReelPort):
    """Compatibility-only Reel adapter using only canonical typed settings."""

    def __init__(self, browser: BrowserPort, settings: Settings):
        self._browser = browser
        self._settings = settings

    async def download_reel(self, url: str) -> Dict[str, Any]:
        inspection = self._settings.inspect_facebook_cookie()
        output_dir = self._settings.browser_download_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        page = await self._browser.get_page("reel_download")
        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self._settings.page_timeout_seconds * 1000,
            )

            def _run_ytdlp() -> dict[str, Any]:
                options: dict[str, Any] = {
                    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "outtmpl": str(
                        output_dir / "%(upload_date)s_%(id)s_%(title).50s.%(ext)s"
                    ),
                    "quiet": True,
                    "no_warnings": True,
                }
                if inspection.valid:
                    options["cookiefile"] = str(inspection.path)
                with yt_dlp.YoutubeDL(options) as downloader:
                    return downloader.extract_info(url, download=True)

            info = await asyncio.to_thread(_run_ytdlp)
            requested = info.get("requested_downloads") if info else None
            video_path = requested[0].get("filepath") if requested else None
            if not video_path and info:
                video_path = info.get("_filename")
            return {
                "url": url,
                "status": "downloaded",
                "title": info.get("title") if info else None,
                "video_path": video_path,
                "authentication_method": inspection.authentication_method,
            }
        finally:
            await page.close()

    async def extract_metadata(self, url: str) -> Dict[str, Any]:
        raise RuntimeError(
            "Standalone Reel metadata extraction is disabled. Use PROCESS_WORKFLOW."
        )
