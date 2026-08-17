"""Output parser — converts raw LLM text into structured ClinicalAnalysisResult.

Responsibilities
----------------
1. Strip Markdown fences (```json ... ```).
2. Attempt JSON parse.
3. Validate required fields.
4. Enforce hard invariants (requires_human_review, visual_analysis_performed).
5. Check for prompt-injection artifacts in the output.
6. Build the clinical_factors_text in the Vietnamese format expected by CDHA web.
7. Apply field-length limits.

All parsing is stateless and dependency-free to keep it easily testable.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from app.ai.models import (
    ANALYSIS_MODE_TEXT,
    ANALYSIS_MODE_VISION,
    ClinicalAnalysisResult,
    ClinicalFinding,
    FrameEvidence,
)

# Fields that every valid output must contain
_REQUIRED_JSON_KEYS = frozenset(
    {
        "analysis_mode",
        "clinical_factors",
        "requires_human_review",
        "visual_analysis_performed",
    }
)

# Clinical Factors headings that must appear in the formatted text
_REQUIRED_CF_HEADINGS = (
    "Cơ quan/vùng khảo sát:",
    "Bên khảo sát:",
    "Cấu trúc đích:",
    "Mặt cắt:",
    "Triệu chứng chính:",
    "Thời gian xuất hiện triệu chứng:",
    "Chỉ định hoặc nghi ngờ lâm sàng:",
    "Dấu hiệu quan sát trực tiếp:",
    "Chỉ định được nguồn nêu rõ:",
    "Nhận định suy ra từ hình ảnh:",
    "Mức độ chắc chắn:",
    "Bằng chứng:",
    "Tiền sử liên quan:",
    "Kết quả xét nghiệm liên quan:",
    "Thông tin bổ sung:",
    "Thông tin chưa được cung cấp:",
)

_MAX_FIELD_CHARS = 5_000
_MAX_TOTAL_CHARS = 30_000

# Markers that suggest the model echoed instructions or injection content
_INJECTION_OUTPUT_MARKERS = (
    "ignore previous instructions",
    "ignore all instructions",
    "reveal the system prompt",
    "developer message",
    "<untrusted_facebook_content>",
    "</untrusted_facebook_content>",
    "<trusted_operator_notes>",
    "<frame_manifest>",
)


class OllamaOutputParser:
    """Parse and validate raw Ollama model output."""

    def parse(
        self,
        raw: str | None,
        *,
        job_id: str,
        frames_were_sent: bool,
        model: str = "",
        prompt_version: str = "ollama-clinical-v1",
    ) -> ClinicalAnalysisResult:
        """Convert raw model output into a validated ClinicalAnalysisResult.

        Returns a result with ``success=False`` and a descriptive ``error``
        when validation cannot be recovered.
        """
        from datetime import UTC, datetime

        generated_at = datetime.now(UTC)
        failures: list[str] = []
        warnings: list[str] = []

        # --- 1. Strip and decode ---
        text = str(raw or "").strip()
        if not text:
            return ClinicalAnalysisResult(
                success=False,
                job_id=job_id,
                model=model,
                generated_at=generated_at,
                error="Model returned an empty response",
            )

        json_text = self._strip_markdown_fence(text)
        data: dict[str, Any] | None = None
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            # Attempt to locate a JSON object inside free-form text
            match = re.search(r"\{.*\}", json_text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        if data is None:
            # Fall back to plain text interpretation
            return self._parse_plain_text(
                text, job_id=job_id, model=model,
                frames_were_sent=frames_were_sent,
                generated_at=generated_at,
                prompt_version=prompt_version,
            )

        # --- 2. Required keys ---
        missing_keys = _REQUIRED_JSON_KEYS - set(data.keys())
        if missing_keys:
            failures.append(f"Missing required JSON keys: {sorted(missing_keys)}")

        # --- 3. Hard invariants ---
        if data.get("requires_human_review") is not True:
            failures.append("requires_human_review must be true")

        reported_visual = bool(data.get("visual_analysis_performed", False))
        if reported_visual and not frames_were_sent:
            failures.append(
                "visual_analysis_performed=true but no frames were sent to the model"
            )

        # --- 4. Mode consistency ---
        analysis_mode = str(data.get("analysis_mode", ANALYSIS_MODE_TEXT))
        if analysis_mode not in {ANALYSIS_MODE_VISION, ANALYSIS_MODE_TEXT}:
            warnings.append(f"Unknown analysis_mode value: {analysis_mode!r}; defaulting to TEXT_ONLY")
            analysis_mode = ANALYSIS_MODE_TEXT

        if analysis_mode == ANALYSIS_MODE_TEXT and reported_visual:
            failures.append("TEXT_ONLY result claims visual_analysis_performed=true")

        # --- 5. Injection artifact check ---
        lowered_raw = text.casefold()
        for marker in _INJECTION_OUTPUT_MARKERS:
            if marker in lowered_raw:
                failures.append(f"Output contains injection-like content: {marker!r}")

        # --- 6. Clinical factors text ---
        cf_raw = data.get("clinical_factors") or ""
        if isinstance(cf_raw, dict):
            cf_text = self._dict_to_cf_text(cf_raw)
        elif isinstance(cf_raw, str):
            # Clean up string if the model output plain text with placeholders
            cleaned_lines = []
            raw_lines = cf_raw.strip().splitlines()
            for line in raw_lines:
                clean_line = line.strip()
                if not clean_line:
                    cleaned_lines.append(line)
                    continue
                if clean_line.startswith("<") and clean_line.endswith(">"):
                    continue
                lowered = clean_line.casefold()
                if lowered in ("không được cung cấp", "null", "none", "không rõ", "không có", "n/a", "không"):
                    continue
                cleaned_lines.append(line)

            # Remove empty headers (lines ending with ':' and followed by empty/EOF)
            final_lines = []
            for i, line in enumerate(cleaned_lines):
                if line.strip().endswith(":"):
                    # Check if there is a non-empty line after this header before the next header
                    has_content = False
                    for j in range(i + 1, len(cleaned_lines)):
                        next_clean = cleaned_lines[j].strip()
                        if not next_clean:
                            continue
                        if next_clean.endswith(":"):
                            break
                        has_content = True
                        break
                    if not has_content:
                        continue
                final_lines.append(line)

            # Strip multiple empty lines
            cf_text = re.sub(r'\n{3,}', '\n\n', "\n".join(final_lines)).strip()
        else:
            cf_text = ""
            failures.append("clinical_factors field must be a string or object")

        # Validate headings are not strictly required since we now omit empty ones
        missing_headings = []

        # Length guard
        if len(cf_text) > _MAX_FIELD_CHARS:
            cf_text = cf_text[:_MAX_FIELD_CHARS - 1].rstrip() + "…"
            warnings.append(f"clinical_factors text truncated to {_MAX_FIELD_CHARS} chars")

        # --- 7. Structured findings ---
        findings = self._parse_findings(data.get("findings", []), frames_were_sent=frames_were_sent)

        # --- 8. Build result ---
        result = ClinicalAnalysisResult(
            success=not failures,
            job_id=job_id,
            model=model,
            analysis_mode=analysis_mode,
            visual_analysis_performed=frames_were_sent and reported_visual,
            prompt_version=prompt_version,
            case_title=str(data.get("case_title", ""))[:200],
            modality=str(data.get("modality", "Ultrasound"))[:100],
            clinical_factors_text=cf_text,
            findings=findings,
            impression=self._str_list(data.get("impression", [])),
            differential_diagnosis=self._str_list(data.get("differential_diagnosis", [])),
            limitations=self._str_list(data.get("limitations", [])),
            safety_notes=self._str_list(data.get("safety_notes", [])),
            overall_confidence=str(data.get("overall_confidence", "LOW")).upper(),
            requires_human_review=True,   # Always True — enforce regardless of model output
            validation_warnings=warnings,
            missing_fields=list(missing_keys) + missing_headings,
            generated_at=generated_at,
            error="; ".join(failures) if failures else None,
        )
        return result

    # ------------------------------------------------------------------
    # Plain text fallback
    # ------------------------------------------------------------------

    def _parse_plain_text(
        self,
        text: str,
        *,
        job_id: str,
        model: str,
        frames_were_sent: bool,
        generated_at: Any,
        prompt_version: str,
    ) -> ClinicalAnalysisResult:
        """Parse structured Vietnamese plain-text output (the primary format).

        Extracts NHẬN ĐỊNH CA BỆNH as the impression and uses the full
        text as clinical_factors_text for CDHA web submission.
        """
        warnings: list[str] = []
        failures: list[str] = []

        lowered = text.casefold()

        for marker in _INJECTION_OUTPUT_MARKERS:
            if marker in lowered:
                failures.append(f"Output contains injection-like content: {marker!r}")

        # Strip placeholder lines like "[Ghi tên cơ quan...]" that model echoed back
        cleaned_lines = []
        raw_lines = text.strip().splitlines()
        for line in raw_lines:
            clean_line = line.strip()
            if not clean_line:
                cleaned_lines.append(line)
                continue
            if clean_line.startswith("<") and clean_line.endswith(">"):
                continue
            if clean_line.startswith("[") and clean_line.endswith("]"):
                continue
            lower_val = clean_line.casefold()
            if lower_val in ("không được cung cấp", "null", "none", "không rõ", "không có", "n/a", "không"):
                continue
            cleaned_lines.append(line)

        # Remove header lines that have no content following them
        final_lines: list[str] = []
        for i, line in enumerate(cleaned_lines):
            if line.strip().endswith(":"):
                has_content = False
                for j in range(i + 1, len(cleaned_lines)):
                    next_clean = cleaned_lines[j].strip()
                    if not next_clean:
                        continue
                    if next_clean.endswith(":"):
                        break
                    has_content = True
                    break
                if not has_content:
                    continue
            final_lines.append(line)

        filtered_text = re.sub(r'\n{3,}', '\n\n', "\n".join(final_lines)).strip()
        cf_text = filtered_text[:_MAX_FIELD_CHARS] if len(filtered_text) > _MAX_FIELD_CHARS else filtered_text

        # Extract the NHẬN ĐỊNH CA BỆNH section as impression
        impression = self._extract_section(filtered_text, "NHẬN ĐỊNH CA BỆNH")
        if not impression:
            # Fallback: look for the last non-empty paragraph as impression
            paragraphs = [p.strip() for p in filtered_text.split("\n\n") if p.strip()]
            if paragraphs:
                impression = paragraphs[-1]

        # Extract CHẨN ĐOÁN PHÂN BIỆT lines as findings
        dd_text = self._extract_section(filtered_text, "CHẨN ĐOÁN PHÂN BIỆT")
        differential: list[str] = []
        if dd_text:
            for dd_line in dd_text.splitlines():
                dd_val = re.sub(r'^\s*[-•*]\s*', '', dd_line).strip()
                if dd_val:
                    differential.append(dd_val)

        return ClinicalAnalysisResult(
            success=not failures,
            job_id=job_id,
            model=model,
            analysis_mode=ANALYSIS_MODE_TEXT if not frames_were_sent else ANALYSIS_MODE_VISION,
            visual_analysis_performed=frames_were_sent,
            prompt_version=prompt_version,
            clinical_factors_text=cf_text,
            impression=[impression] if impression else [],
            differential_diagnosis=differential,
            requires_human_review=True,
            validation_warnings=warnings,
            missing_fields=[],
            generated_at=generated_at,
            error="; ".join(failures) if failures else None,
        )

    @staticmethod
    def _extract_section(text: str, header: str) -> str:
        """Extract the content of a Vietnamese plain-text section by header name."""
        # Match header at start of line (case-insensitive, with optional colon)
        pattern = re.compile(
            rf"(?:^|\n){re.escape(header)}\s*:?\s*\n([\s\S]*?)(?:\n[A-ZÀ-ỹ][A-ZÀ-ỹ\s/]+:\s*\n|$)",
            re.IGNORECASE,
        )
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
        return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_markdown_fence(text: str) -> str:
        text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text.strip(), flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def _dict_to_cf_text(cf: dict[str, Any]) -> str:
        """Convert a structured dict into the Vietnamese plain-text CF format."""
        mapping = {
            "organ_region": "Cơ quan/vùng khảo sát",
            "laterality": "Bên khảo sát",
            "target_structure": "Cấu trúc đích",
            "scan_plane": "Mặt cắt",
            "main_symptoms": "Triệu chứng chính",
            "symptom_duration": "Thời gian xuất hiện triệu chứng",
            "clinical_indication": "Chỉ định hoặc nghi ngờ lâm sàng",
            "direct_observations": "Dấu hiệu quan sát trực tiếp",
            "source_stated_indication": "Chỉ định được nguồn nêu rõ",
            "image_inference": "Nhận định suy ra từ hình ảnh",
            "confidence": "Mức độ chắc chắn",
            "evidence": "Bằng chứng",
            "relevant_history": "Tiền sử liên quan",
            "relevant_lab_results": "Kết quả xét nghiệm liên quan",
            "additional_info": "Thông tin bổ sung",
            "missing_info": "Thông tin chưa được cung cấp",
        }
        lines: list[str] = []
        for key, label in mapping.items():
            raw_value = cf.get(key)
            if raw_value is None:
                continue
            value = str(raw_value).strip()

            # Skip empty, null, or placeholder values
            lowered = value.casefold()
            if (
                not value
                or lowered in ("không được cung cấp", "null", "none", "không rõ", "không có", "n/a", "không")
                or (value.startswith("<") and value.endswith(">"))
            ):
                continue

            lines.append(f"{label}:\n{value}")
        return "\n\n".join(lines)

    @staticmethod
    def _str_list(value: Any) -> list[str]:
        if isinstance(value, list):
            result = []
            for item in value:
                if not item:
                    continue
                s = str(item).strip()
                if s and not (s.startswith("<") and s.endswith(">")):
                    result.append(s)
            return result
        if isinstance(value, str) and value.strip():
            s = value.strip()
            if not (s.startswith("<") and s.endswith(">")):
                return [s]
        return []

    @staticmethod
    def _parse_findings(raw: Any, *, frames_were_sent: bool) -> list[ClinicalFinding]:
        if not isinstance(raw, list):
            return []
        findings: list[ClinicalFinding] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            evidence: list[FrameEvidence] = []
            if frames_were_sent:
                for ef in item.get("evidence_frames", []):
                    if isinstance(ef, dict):
                        evidence.append(
                            FrameEvidence(
                                frame_id=str(ef.get("frame_id", "")),
                                timestamp_seconds=float(ef.get("timestamp_seconds", 0.0)),
                            )
                        )
            findings.append(
                ClinicalFinding(
                    description=str(item.get("description", ""))[:500],
                    evidence_frames=evidence,
                    confidence=str(item.get("confidence", "LOW")).upper(),
                )
            )
        return findings
