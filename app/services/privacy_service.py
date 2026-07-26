from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence

PHONE_TOKEN = "[ĐÃ ẨN SỐ ĐIỆN THOẠI]"
EMAIL_TOKEN = "[ĐÃ ẨN EMAIL]"
PATIENT_ID_TOKEN = "[ĐÃ ẨN MÃ BỆNH NHÂN]"
MEDICAL_RECORD_TOKEN = "[ĐÃ ẨN MÃ HỒ SƠ]"
IDENTITY_TOKEN = "[ĐÃ ẨN THÔNG TIN ĐỊNH DANH]"
MEDIA_PII_WARNING = (
    "Quét văn bản không thể phát hiện PII nằm trong video, khung hình hoặc ảnh chụp; "
    "người vận hành phải kiểm tra trực quan trước khi phê duyệt."
)

@dataclass(frozen=True, slots=True)
class PIIMatch:
    """PII finding metadata; deliberately never contains the matched value."""
    category: str
    start: int
    end: int
    confidence: float
    detector: str

@dataclass(frozen=True, slots=True)
class PrivacyScanResult:
    safe_to_continue: bool
    risk_level: str
    detected_categories: tuple[str, ...]
    total_matches: int
    requires_manual_review: bool
    warnings: tuple[str, ...]

class PIIDetector(Protocol):
    def detect(self, text: str) -> Sequence[PIIMatch]: ...

@dataclass(frozen=True, slots=True)
class _PatternSpec:
    category: str
    pattern: re.Pattern[str]
    token: str
    confidence: float = 0.95

class RegexPIIDetector:
    def __init__(self, patterns: Sequence[_PatternSpec]):
        self.patterns = tuple(patterns)

    def detect(self, text: str) -> tuple[PIIMatch, ...]:
        findings: list[PIIMatch] = []
        occupied: list[tuple[int, int]] = []
        for spec in self.patterns:
            for match in spec.pattern.finditer(text):
                span = match.span()
                if any(span[0] < end and span[1] > start for start, end in occupied):
                    continue
                occupied.append(span)
                findings.append(PIIMatch(spec.category, span[0], span[1], spec.confidence, "regex"))
        return tuple(sorted(findings, key=lambda item: (item.start, item.end)))

class CompositePIIDetector:
    def __init__(self, detectors: Sequence[PIIDetector]):
        self.detectors = tuple(detectors)

    def detect(self, text: str) -> tuple[PIIMatch, ...]:
        findings: list[PIIMatch] = []
        for detector in self.detectors:
            findings.extend(detector.detect(text))
        return tuple(sorted(findings, key=lambda item: (item.start, item.end)))

class PrivacyService:
    """Masks explicit identifiers and reports only non-sensitive finding metadata."""
    _email = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)")
    _obfuscated_email = re.compile(
        r"(?<!\w)[\w.+-]+\s*(?:\[at\]|\(at\)|\sat\s)\s*[\w.-]+"
        r"\s*(?:\[dot\]|\(dot\)|\sdot\s)\s*[A-Za-z]{2,}(?!\w)", re.IGNORECASE,
    )
    _phone = re.compile(r"(?<!\w)(?:\+?84[ .-]?|0)(?:\d[ .-]?){8,10}(?!\w)", re.IGNORECASE)
    _medical_record = re.compile(
        r"\b(?:mã\s*(?:hồ\s*sơ|hs)|mrn|medical\s*record(?:\s*id)?)"
        r"\s*[:#-]?\s*[A-Z0-9][A-Z0-9._/-]{2,}\b", re.IGNORECASE,
    )
    _patient_id = re.compile(
        r"\b(?:mã\s*(?:bệnh\s*nhân|bn)|patient\s*id)"
        r"\s*[:#-]?\s*[A-Z0-9][A-Z0-9._/-]{2,}\b", re.IGNORECASE,
    )
    _national_id = re.compile(
        r"\b(?:(?:cccd|cmnd|căn\s*cước|national\s*id)\s*[:#-]?\s*)?\d{9,12}\b", re.IGNORECASE,
    )
    _url = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
    _handle = re.compile(r"(?<![\w@])@[A-Za-z0-9._-]{2,}")
    _explicit_name = re.compile(
        r"\b(?:họ\s*(?:và\s*)?tên|tên\s*bệnh\s*nhân|bệnh\s*nhân|patient\s*name)"
        r"\s*:\s*[A-ZÀ-Ỹ][A-Za-zÀ-ỹ]*(?:\s+[A-ZÀ-Ỹ][A-Za-zÀ-ỹ]*){1,5}", re.IGNORECASE,
    )
    _explicit_identity = re.compile(
        r"\b(?:địa\s*chỉ|address|tài\s*khoản|account)\s*:\s*[^;\n]{3,}", re.IGNORECASE,
    )
    _pattern_specs = (
        _PatternSpec("email", _email, EMAIL_TOKEN),
        _PatternSpec("obfuscated_email", _obfuscated_email, EMAIL_TOKEN, 0.85),
        _PatternSpec("medical_record", _medical_record, MEDICAL_RECORD_TOKEN),
        _PatternSpec("patient_id", _patient_id, PATIENT_ID_TOKEN),
        _PatternSpec("phone", _phone, PHONE_TOKEN),
        _PatternSpec("national_id", _national_id, IDENTITY_TOKEN),
        _PatternSpec("url", _url, IDENTITY_TOKEN, 0.8),
        _PatternSpec("social_handle", _handle, IDENTITY_TOKEN, 0.85),
        _PatternSpec("explicit_name", _explicit_name, IDENTITY_TOKEN, 0.9),
        _PatternSpec("explicit_identity", _explicit_identity, IDENTITY_TOKEN, 0.9),
    )

    def __init__(self, detector: PIIDetector | None = None):
        self.detector = detector or CompositePIIDetector((RegexPIIDetector(self._pattern_specs),))

    def mask(self, text: str | None, *, full_names: tuple[str, ...] = ()) -> str:
        value = str(text or "")
        for name in sorted({item.strip() for item in full_names if item.strip()}, key=len, reverse=True):
            value = re.sub(re.escape(name), IDENTITY_TOKEN, value, flags=re.IGNORECASE)
        for spec in self._pattern_specs:
            value = spec.pattern.sub(spec.token, value)
        return value

    def scan(self, text: str | None) -> PrivacyScanResult:
        findings = tuple(self.detector.detect(str(text or "")))
        categories = tuple(sorted({item.category for item in findings}))
        high_risk = {"national_id", "medical_record", "patient_id", "explicit_name"}
        risk = "HIGH" if high_risk.intersection(categories) else "MEDIUM" if findings else "LOW"
        warnings = [MEDIA_PII_WARNING]
        if findings:
            warnings.insert(0, "Phát hiện định danh trong văn bản; chỉ metadata đã được ghi nhận.")
        return PrivacyScanResult(
            safe_to_continue=not findings,
            risk_level=risk,
            detected_categories=categories,
            total_matches=len(findings),
            requires_manual_review=bool(findings),
            warnings=tuple(warnings),
        )

    def contains_obvious_identifier(self, text: str | None) -> bool:
        return bool(self.detector.detect(str(text or "")))
