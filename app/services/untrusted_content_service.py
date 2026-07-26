from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class SanitizedExternalContent:
    normalized_text: str
    original_length: int
    truncated: bool
    suspicious_patterns: tuple[str, ...]
    risk_level: str

class UntrustedContentService:
    """Normalizes external text and labels prompt-injection signals without executing them."""
    _patterns = (
        ("ignore_instructions", re.compile(r"\bignore\s+(?:all\s+|previous\s+)?instructions?\b", re.I), True),
        ("reveal_system_prompt", re.compile(r"\b(?:reveal|show|print|repeat)\s+(?:the\s+)?system\s+prompt\b", re.I), True),
        ("developer_message", re.compile(r"\bdeveloper\s+(?:message|instructions?)\b", re.I), False),
        ("role_override", re.compile(r"\b(?:act\s+as|you\s+are\s+now)\b", re.I), False),
        ("output_override", re.compile(r"\breturn\s+only\b", re.I), False),
        ("security_bypass", re.compile(r"\b(?:override|bypass)\s+(?:safety|security|rules?|policy)\b", re.I), True),
        ("analysis_suppression", re.compile(r"\bdo\s+not\s+analy[sz]e\b", re.I), True),
        ("follow_external_instructions", re.compile(r"\bfollow\s+(?:my|these|the following)\s+instructions?\b", re.I), False),
    )

    def __init__(self, *, max_chars: int = 30_000):
        self.max_chars = max_chars

    def sanitize(self, text: str | None, *, max_chars: int | None = None) -> SanitizedExternalContent:
        raw = str(text or "")
        value = unicodedata.normalize("NFKC", raw)
        value = "".join(
            char for char in value
            if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf"}
        )
        value = "\n".join(" ".join(line.split()) for line in value.splitlines()).strip()
        limit = self.max_chars if max_chars is None else max_chars
        truncated = len(value) > limit
        if truncated:
            value = value[: max(0, limit - 1)].rstrip() + "…"
        matches = tuple(name for name, pattern, _ in self._patterns if pattern.search(value))
        severe = any(pattern.search(value) for _, pattern, is_severe in self._patterns if is_severe)
        risk = "HIGH" if severe or len(matches) >= 2 else "MEDIUM" if matches else "LOW"
        return SanitizedExternalContent(value, len(raw), truncated, matches, risk)
