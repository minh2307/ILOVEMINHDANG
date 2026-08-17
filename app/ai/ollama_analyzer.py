"""OllamaAnalyzer — high-level orchestrator implementing AIClinicalAnalyzer.

Responsibilities
----------------
1. Capability check: determine VISION or TEXT_ONLY mode.
2. Build versioned, sandboxed prompts (trusted instruction + untrusted content).
3. Call OllamaClient.chat().
4. Delegate output parsing to OllamaOutputParser.
5. Apply PrivacyService masking to the result text.
6. Write artifacts atomically (normalized, masked, raw if enabled).
7. Record job events with non-sensitive metadata.
8. Satisfy the AIClinicalAnalyzer Protocol.

Security invariants
-------------------
* Raw model output is NEVER written to structured logs.
* Untrusted Facebook content is always wrapped in <UNTRUSTED_FACEBOOK_CONTENT>.
* Trusted operator notes are wrapped in <TRUSTED_OPERATOR_NOTES>.
* requires_human_review is forced to True in the final result regardless of
  model output.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ai.capability import ModelCapabilities
from app.ai.exceptions import ModelCapabilityError, AIConnectionError, AIProviderError
from app.ai.models import (
    ANALYSIS_MODE_TEXT,
    ANALYSIS_MODE_VISION,
    AIHealthStatus,
    ClinicalAnalysisRequest,
    ClinicalAnalysisResult,
)
from app.ai.ollama_client import OllamaClient
from app.ai.output_parser import OllamaOutputParser
from app.services.privacy_service import PrivacyService
from app.services.untrusted_content_service import UntrustedContentService


# ---------------------------------------------------------------------------
# Versioned prompt templates
# ---------------------------------------------------------------------------

_PROMPT_V1_INSTRUCTION = """\
Bạn là AI hỗ trợ tạo GHI CHÚ LÂM SÀNG từ hình ảnh y khoa và thông tin mô tả được cung cấp.

NHIỆM VỤ:
Phân tích hình ảnh và các thông tin đầu vào, sau đó viết một ghi chú lâm sàng ngắn gọn bằng TIẾNG VIỆT để hiển thị trực tiếp cho bác sĩ/người dùng.

NGUYÊN TẮC QUAN TRỌNG:
1. CHỈ sử dụng thông tin có trong hình ảnh và dữ liệu đầu vào.
2. Không được tự tạo ra bệnh, tổn thương, triệu chứng, kích thước, vị trí hoặc kết quả xét nghiệm nếu những thông tin đó không được thể hiện rõ.
3. Không biến một khả năng hoặc nghi ngờ thành chẩn đoán xác định.
4. Nếu thông tin chỉ gợi ý một bất thường, hãy sử dụng các cách diễn đạt như "gợi ý", "có thể phù hợp với", "nghi ngờ" hoặc "chưa đủ cơ sở xác định".
5. Không tự suy luận mức độ nguy hiểm, nguy cơ ác tính hoặc tiên lượng nếu dữ liệu đầu vào không cung cấp cơ sở rõ ràng.
6. Không tự tạo hoặc thay đổi phân loại y khoa như TI-RADS, BI-RADS, FIGO hoặc các hệ thống phân loại khác nếu không có thông tin đầy đủ để xác định.
7. Không đưa ra khuyến nghị điều trị hoặc thủ thuật nếu dữ liệu đầu vào không cung cấp hoặc không đủ cơ sở.
8. Không sử dụng kiến thức bên ngoài để bổ sung các thông tin còn thiếu.
9. Nếu hình ảnh hoặc dữ liệu không đủ để nhận định, hãy nói rõ rằng thông tin hiện tại chưa đủ để đưa ra kết luận chắc chắn.
10. Không lặp lại từ, cụm từ hoặc chẩn đoán.
11. Không sử dụng tiếng Trung, tiếng Nhật, tiếng Hàn hoặc ngôn ngữ khác. CHỈ sử dụng tiếng Việt.
12. Không tạo thông tin giả chỉ để làm cho câu trả lời đầy đủ hơn.

QUY TẮC OUTPUT BẮT BUỘC:
- Chỉ trả về DUY NHẤT MỘT ĐOẠN VĂN.
- Không được xuống dòng.
- Không dùng Markdown.
- Không dùng bullet list.
- Không dùng JSON.
- Không dùng XML.
- Không dùng bảng.
- Không dùng tiêu đề.
- Không sử dụng các nhãn như "IMPRESSION:", "FINDINGS:", "DIAGNOSIS:", "RECOMMENDATION:" hoặc "DIFFERENTIAL DIAGNOSIS:".
- Không thêm lời chào.
- Không giải thích quá trình suy luận.
- Không nói "Tôi là AI".
- Không thêm disclaimer dài.
- Không lặp lại nội dung.
- Độ dài ưu tiên khoảng 1–3 câu.
- Văn phong phải giống một ghi chú lâm sàng ngắn gọn, khách quan và chuyên nghiệp.
- Output phải có thể được đưa trực tiếp vào trường "Ghi chú lâm sàng".

CẤU TRÚC NỘI DUNG:
Nếu có đủ thông tin:
"Mô tả phát hiện chính + đặc điểm quan sát được + nhận định thận trọng nếu có cơ sở."

Nếu chưa đủ thông tin:
"Thông tin hình ảnh hiện tại chưa đủ để đưa ra kết luận chắc chắn."
"""

_PROMPT_V1_TEXT_ONLY_FOOTER = """
LƯU Ý QUAN TRỌNG: Yêu cầu này KHÔNG có ảnh hoặc video đính kèm.
Chỉ đưa ra nhận định dựa trên nội dung caption và bình luận.
"""

_PROMPT_V1_VISION_FOOTER = """
LƯU Ý QUAN TRỌNG: Các frame từ video được đính kèm trong yêu cầu này.
Hãy đưa ra nhận định trực tiếp dựa trên nội dung hình ảnh quan sát được.
"""


class OllamaAnalyzer:
    """Implements AIClinicalAnalyzer using Ollama local LLM.

    Parameters
    ----------
    client : OllamaClient
        Configured low-level HTTP client.
    job_data_dir : Path
        Root directory for per-job artifacts.
    privacy : PrivacyService | None
        PII masking service.
    untrusted : UntrustedContentService | None
        Input sanitization service.
    save_raw_response : bool
        If True, write raw model output to ``<job_dir>/ollama-response-raw.txt``.
        Disabled by default (sensitive medical content).
    logger : logging.Logger | None
        Optional logger.
    """

    def __init__(
        self,
        client: OllamaClient,
        *,
        job_data_dir: Path,
        privacy: PrivacyService | None = None,
        untrusted: UntrustedContentService | None = None,
        save_raw_response: bool = False,
        max_comment_chars: int = 600,
        max_comments: int = 100,
        max_total_comment_chars: int = 15_000,
        max_prompt_chars: int = 30_000,
        max_response_chars: int = 5_000,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._job_data_dir = job_data_dir
        self._privacy = privacy or PrivacyService()
        self._untrusted = untrusted or UntrustedContentService(max_chars=max_prompt_chars)
        self._save_raw = save_raw_response
        self._max_comment_chars = max_comment_chars
        self._max_comments = max_comments
        self._max_total_comment_chars = max_total_comment_chars
        self._max_prompt_chars = max_prompt_chars
        self._max_response_chars = max_response_chars
        self._parser = OllamaOutputParser()
        self._logger = logger or logging.getLogger("cdha_pipeline.ollama_analyzer")

    # ------------------------------------------------------------------
    # AIClinicalAnalyzer protocol implementation
    # ------------------------------------------------------------------

    async def health_check(self) -> AIHealthStatus:
        return await self._client.health_check()

    async def list_models(self) -> tuple[str, ...]:
        """Read-only readiness hook using the official configured client."""
        return await self._client.list_models()

    async def minimal_inference(self, prompt: str) -> str:
        """Run a non-clinical readiness inference through the official adapter."""
        return await self._client.generate(prompt=prompt, stream=False)

    async def get_capabilities(self) -> ModelCapabilities:
        return await self._client.get_capabilities()

    async def analyze(self, request: ClinicalAnalysisRequest) -> ClinicalAnalysisResult:
        """Orchestrate the full analysis lifecycle."""
        job_id = request.job_id
        job_dir = (self._job_data_dir / job_id).resolve()
        job_dir.mkdir(parents=True, exist_ok=True)

        # --- Capability check ---
        caps = await self.get_capabilities()
        frames_available = bool(request.frame_paths)
        use_vision = caps.supports_images and frames_available

        if frames_available and not caps.supports_images:
            raise ModelCapabilityError(
                f"Model '{self._client._model}' does not support vision but frame paths were "
                f"provided. Reconfigure OLLAMA_MODEL to a vision model or set "
                f"FRAME_EXTRACTION_ENABLED=false to fall back to TEXT_ONLY analysis."
            )

        effective_mode = ANALYSIS_MODE_VISION if use_vision else ANALYSIS_MODE_TEXT

        # --- Assess input risk ---
        source_content = request.caption + "\n" + "\n".join(
            str(c.get("content", "")) for c in request.comments
        )
        assessment = self._untrusted.sanitize(
            self._privacy.mask(source_content), max_chars=self._max_prompt_chars
        )
        if assessment.risk_level != "LOW":
            self._logger.warning(
                "Elevated input risk level detected",
                extra={
                    "job_id": job_id,
                    "risk_level": assessment.risk_level,
                    "suspicious_patterns": list(assessment.suspicious_patterns),
                },
            )

        # --- Build prompt messages ---
        messages = self._build_messages(request, mode=effective_mode)

        # --- Attach frames if vision ---
        if use_vision:
            messages = self._attach_frames(messages, request.frame_paths)

        frames_were_sent = use_vision and bool(request.frame_paths)

        # --- Call model ---
        raw_response = await self._client.chat(messages=messages)

        # --- Parse & validate ---
        result = self._parser.parse(
            raw_response,
            job_id=job_id,
            frames_were_sent=frames_were_sent,
            model=self._client._model,
            prompt_version=request.prompt_version,
        )

        # --- Apply privacy masking to output ---
        masked_cf = self._privacy.mask(result.clinical_factors_text)

        # --- Write artifacts atomically ---
        raw_path: Path | None = None
        if self._save_raw:
            raw_path = job_dir / "ollama-response-raw.txt"
            self._write_atomic(raw_path, raw_response)

        normalized_path = job_dir / "clinical-factors-normalized.txt"
        masked_path = job_dir / "clinical-factors-masked.txt"
        self._write_atomic(normalized_path, result.clinical_factors_text)
        self._write_atomic(masked_path, masked_cf)

        # --- Rebuild result with artifact paths and forced invariants ---
        result = replace(
            result,
            clinical_factors_text=masked_cf,  # always store masked version
            visual_analysis_performed=frames_were_sent,  # enforce
            requires_human_review=True,                  # enforce
            analysis_mode=effective_mode,                # system-determined
            raw_response_path=str(raw_path) if raw_path else None,
            normalized_output_path=str(normalized_path),
            masked_output_path=str(masked_path),
        )

        self._logger.info(
            "Ollama analysis complete",
            extra={
                "job_id": job_id,
                "success": result.success,
                "analysis_mode": effective_mode,
                "frames_sent": frames_were_sent,
                "overall_confidence": result.overall_confidence,
                "validation_warnings": len(result.validation_warnings),
            },
        )
        return result

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        request: ClinicalAnalysisRequest,
        *,
        mode: str,
    ) -> list[dict[str, Any]]:
        """Build the chat messages list with trust boundaries enforced."""
        footer = _PROMPT_V1_VISION_FOOTER if mode == ANALYSIS_MODE_VISION else _PROMPT_V1_TEXT_ONLY_FOOTER

        # Sanitize untrusted content
        clean_caption = self._untrusted.sanitize(
            self._privacy.mask(str(request.caption or "").strip()),
            max_chars=self._max_prompt_chars,
        ).normalized_text or "Không được cung cấp"

        comment_lines: list[str] = []
        seen: set[str] = set()
        total_chars = 0
        for item in request.comments:
            content = str(item.get("content", "")).strip()
            if not content or content in seen:
                continue
            seen.add(content)
            masked = self._untrusted.sanitize(
                self._privacy.mask(content), max_chars=self._max_comment_chars
            ).normalized_text.strip()
            if not masked:
                continue
            if len(masked) > self._max_comment_chars:
                masked = masked[: self._max_comment_chars - 1].rstrip() + "…"
            remaining = self._max_total_comment_chars - total_chars
            if remaining <= 0:
                break
            if len(masked) > remaining:
                masked = masked[: max(0, remaining - 1)].rstrip() + "…"
            comment_lines.append(f"- {masked}")
            total_chars += len(masked)
            if len(comment_lines) >= self._max_comments:
                break
        comments_text = "\n".join(comment_lines) or "Không được cung cấp"

        # Trusted operator notes — NOT sanitized (already trusted)
        operator_notes = str(request.trusted_operator_notes or "").strip() or "Không có"

        # Frame manifest
        frame_manifest = ""
        if request.frame_paths:
            lines = []
            for idx, fp in enumerate(request.frame_paths, 1):
                lines.append(f"Frame {idx}: {Path(fp).name}")
            frame_manifest = (
                "\n<FRAME_MANIFEST>\n" + "\n".join(lines) + "\n</FRAME_MANIFEST>"
            )

        system_content = _PROMPT_V1_INSTRUCTION + footer
        user_content = (
            f"<UNTRUSTED_FACEBOOK_CONTENT>\n"
            f"Caption:\n{clean_caption}\n\n"
            f"Public comments:\n{comments_text}\n"
            f"</UNTRUSTED_FACEBOOK_CONTENT>\n\n"
            f"<TRUSTED_OPERATOR_NOTES>\n{operator_notes}\n</TRUSTED_OPERATOR_NOTES>"
            + frame_manifest
        )

        return [
            {"role": "user", "content": system_content + "\n\n" + user_content},
        ]

    def _attach_frames(
        self, messages: list[dict[str, Any]], frame_paths: list[str]
    ) -> list[dict[str, Any]]:
        """Encode frames as base-64 and attach to the last user message."""
        images: list[str] = []
        for fp in frame_paths:
            try:
                images.append(OllamaClient.encode_image(Path(fp)))
            except OSError as exc:
                self._logger.warning(
                    "Could not read frame file; skipping",
                    extra={"path": fp, "error": str(exc)},
                )
        if not images:
            return messages
        # Ollama multimodal: add images list to the last user message
        last = dict(messages[-1])
        last["images"] = images
        return messages[:-1] + [last]

    # ------------------------------------------------------------------
    # Atomic file I/O
    # ------------------------------------------------------------------

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        path.chmod(0o600)
