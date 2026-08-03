from dataclasses import dataclass
from typing import Any

@dataclass
class BrowserSession:
    session_id: str
    context: Any  # E.g., Playwright BrowserContext
    is_active: bool = True
