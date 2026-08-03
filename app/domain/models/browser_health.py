from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BrowserHealthState(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    CONTEXT_CLOSED = "CONTEXT_CLOSED"
    PAGE_CLOSED = "PAGE_CLOSED"
    PROFILE_BUSY = "PROFILE_BUSY"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class BrowserHealth:
    state: BrowserHealthState
    browser_connected: bool
    context_available: bool
    page_available: bool | None = None
    current_url: str | None = None
    detail: str | None = None

