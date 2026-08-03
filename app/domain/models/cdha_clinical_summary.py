from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


class ClinicalSummaryValidationError(ValueError):
    """Raised when a CDHA result cannot safely supply publishable clinical data."""


_FINDING_LABEL = re.compile(
    r"^\s*(?:key\s*findings?|findings?|phát\s*hiện\s*chính|ghi\s*nhận\s*chính)\s*:?\s*",
    re.IGNORECASE,
)
_IMPRESSION_LABEL = re.compile(
    r"^\s*(?:impression|nhận\s*định|kết\s*luận)\s*:?\s*",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[-*•‣▪◦]+|\d+[.)])\s*")


@dataclass(frozen=True, slots=True)
class CDHAClinicalSummary:
    key_findings: list[str]
    impression: str
    analysis_url: str
    source_language: str | None = None
    raw_key_findings: str | None = None
    raw_impression: str | None = None

    @classmethod
    def from_values(
        cls,
        *,
        key_findings: Iterable[object],
        impression: object,
        analysis_url: str,
        source_language: str | None = None,
        raw_key_findings: str | None = None,
        raw_impression: str | None = None,
    ) -> "CDHAClinicalSummary":
        raw_findings = raw_key_findings
        if raw_findings is None:
            raw_findings = "\n".join(str(item) for item in key_findings if item is not None)
        raw_impression_value = raw_impression
        if raw_impression_value is None:
            raw_impression_value = str(impression or "")

        findings = cls.normalize_key_findings(raw_findings)
        normalized_impression = cls.normalize_impression_text(raw_impression_value)
        if not findings:
            raise ClinicalSummaryValidationError(
                "CDHA Key Findings are missing or contain only a field label"
            )
        if not normalized_impression:
            raise ClinicalSummaryValidationError(
                "CDHA Impression is missing or contains only a field label"
            )
        cls._validate_url(analysis_url)
        return cls(
            key_findings=findings,
            impression=normalized_impression,
            analysis_url=analysis_url.strip(),
            source_language=str(source_language).strip() if source_language else None,
            raw_key_findings=raw_findings or None,
            raw_impression=raw_impression_value or None,
        )

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, Any], *, analysis_url: str = ""
    ) -> "CDHAClinicalSummary":
        raw_findings = payload.get("raw_key_findings")
        raw_impression = payload.get("raw_impression")
        findings = payload.get("key_findings") or []
        if isinstance(findings, str):
            findings = [findings]
        return cls.from_values(
            key_findings=findings,
            impression=payload.get("impression"),
            analysis_url=analysis_url or str(payload.get("analysis_url") or ""),
            source_language=payload.get("source_language"),
            raw_key_findings=str(raw_findings) if raw_findings is not None else None,
            raw_impression=str(raw_impression) if raw_impression is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key_findings": list(self.key_findings),
            "impression": self.impression,
            "analysis_url": self.analysis_url,
            "source_language": self.source_language,
            "raw_key_findings": self.raw_key_findings,
            "raw_impression": self.raw_impression,
        }

    @staticmethod
    def normalize_key_findings(raw: str) -> list[str]:
        findings: list[str] = []
        for line in str(raw or "").splitlines():
            value = _BULLET.sub("", line).strip()
            value = _FINDING_LABEL.sub("", value).strip()
            if not value or _FINDING_LABEL.fullmatch(line.strip()):
                continue
            if value not in findings:
                findings.append(value)
        return findings

    @staticmethod
    def normalize_impression_text(raw: str) -> str:
        values: list[str] = []
        for line in str(raw or "").splitlines():
            value = _BULLET.sub("", line).strip()
            value = _IMPRESSION_LABEL.sub("", value).strip()
            if value and not _IMPRESSION_LABEL.fullmatch(line.strip()):
                values.append(value)
        return " ".join(values).strip()

    @staticmethod
    def _validate_url(value: str) -> None:
        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise ClinicalSummaryValidationError(
                "An exact HTTPS CDHA analysis URL is required"
            )
