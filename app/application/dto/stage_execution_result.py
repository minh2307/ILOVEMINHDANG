from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class StageExecutionResult:
    success: bool
    error: str | None = None
    pending_manual_action: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
