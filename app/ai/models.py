"""Value objects for the AI provider request / response contract.

These dataclasses are the sole shared data layer between the pipeline and the
AI provider implementations.  All fields use plain Python types so they can be
serialised to JSON and persisted in the SQLite job store.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Analysis mode — set by the system, never by untrusted content
# ---------------------------------------------------------------------------

ANALYSIS_MODE_VISION = "VISION_FRAMES"
ANALYSIS_MODE_TEXT = "TEXT_ONLY"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ClinicalAnalysisRequest:
    """All inputs the AI provider needs to produce a ClinicalFactors result.

    Trusted vs. untrusted
    ---------------------
    ``caption``, ``comments``      — untrusted; originate from Facebook
    ``trusted_operator_notes``     — trusted; entered by an authenticated operator
    ``frame_paths``                — trusted; produced by FrameExtractionService
    All other fields               — trusted; set by the pipeline, not users
    """

    job_id: str
    video_path: str | None = None
    frame_paths: list[str] = field(default_factory=list)

    # Untrusted external content (always treated as user data)
    caption: str = ""
    comments: list[dict[str, Any]] = field(default_factory=list)

    # Trusted operator input
    trusted_operator_notes: str = ""

    # Analysis configuration
    requested_output_language: str = "vi"
    analysis_mode: str = ANALYSIS_MODE_TEXT   # overridden after capability check
    prompt_version: str = "ollama-clinical-v1"

    # Privacy / security metadata produced upstream
    privacy_risk_level: str = "LOW"
    injection_risk: str = "LOW"


# ---------------------------------------------------------------------------
# Response parts
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FrameEvidence:
    """Reference linking a finding back to a specific extracted frame."""
    frame_id: str
    timestamp_seconds: float


@dataclass(frozen=True, slots=True)
class ClinicalFinding:
    description: str
    evidence_frames: list[FrameEvidence] = field(default_factory=list)
    confidence: str = "LOW"   # LOW | MEDIUM | HIGH


@dataclass(frozen=True, slots=True)
class AIHealthStatus:
    """Result of a provider health check."""
    healthy: bool
    provider: str
    model: str
    detail: str = ""
    checked_at: str = ""


# ---------------------------------------------------------------------------
# Main result
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ClinicalAnalysisResult:
    """Structured output from an AI clinical analysis.

    Invariants (enforced by OllamaAnalyzer)
    ----------------------------------------
    * ``requires_human_review`` is always True.
    * ``visual_analysis_performed`` is True only when frames were sent.
    * TEXT_ONLY results carry no ``evidence_frames`` in any finding.
    * The result may be partial on failure (``success=False``).
    """

    success: bool
    job_id: str

    # Schema metadata
    schema_version: str = "1.0"
    provider: str = "ollama"
    model: str = ""
    analysis_mode: str = ANALYSIS_MODE_TEXT
    visual_analysis_performed: bool = False
    prompt_version: str = "ollama-clinical-v1"

    # Clinical content — plain text fields map directly to ClinicalFactors headings
    case_title: str = ""
    modality: str = "Ultrasound"
    clinical_factors_text: str = ""    # formatted for CDHA web upload (same format as Gemini output)

    # Structured findings (optional; used for evidence tracing)
    findings: list[ClinicalFinding] = field(default_factory=list)
    impression: list[str] = field(default_factory=list)
    differential_diagnosis: list[str] = field(default_factory=list)

    # Safety and audit
    limitations: list[str] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=list)
    overall_confidence: str = "LOW"
    requires_human_review: bool = True

    # Paths written by the analyzer
    raw_response_path: str | None = None
    normalized_output_path: str | None = None
    masked_output_path: str | None = None

    # Validation
    validation_warnings: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    # Timestamps
    generated_at: datetime | str | None = None

    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "job_id": self.job_id,
            "schema_version": self.schema_version,
            "provider": self.provider,
            "model": self.model,
            "analysis_mode": self.analysis_mode,
            "visual_analysis_performed": self.visual_analysis_performed,
            "prompt_version": self.prompt_version,
            "case_title": self.case_title,
            "modality": self.modality,
            "clinical_factors_text": self.clinical_factors_text,
            "findings": [
                {
                    "description": f.description,
                    "evidence_frames": [
                        {"frame_id": e.frame_id, "timestamp_seconds": e.timestamp_seconds}
                        for e in f.evidence_frames
                    ],
                    "confidence": f.confidence,
                }
                for f in self.findings
            ],
            "impression": list(self.impression),
            "differential_diagnosis": list(self.differential_diagnosis),
            "limitations": list(self.limitations),
            "safety_notes": list(self.safety_notes),
            "overall_confidence": self.overall_confidence,
            "requires_human_review": self.requires_human_review,
            "raw_response_path": self.raw_response_path,
            "normalized_output_path": self.normalized_output_path,
            "masked_output_path": self.masked_output_path,
            "validation_warnings": list(self.validation_warnings),
            "missing_fields": list(self.missing_fields),
            "generated_at": (
                self.generated_at.isoformat()
                if isinstance(self.generated_at, datetime)
                else self.generated_at
            ),
            "error": self.error,
        }
