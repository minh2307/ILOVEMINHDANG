"""Backward-compatible import for the single shared Facebook CDP manager."""

from app.browser.facebook_browser_manager import (
    FacebookBrowserError as ChromeManagerError,
    FacebookBrowserManager as ChromeManager,
    ProfileInUseError,
)

__all__ = ["ChromeManager", "ChromeManagerError", "ProfileInUseError"]
