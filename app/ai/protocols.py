"""AIClinicalAnalyzer — structural Protocol (PEP 544).

Pipeline code imports and depends only on this interface.  No concrete
implementation details (Ollama, HTTP, model names) must leak into the
orchestration layer.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.ai.capability import ModelCapabilities
from app.ai.models import AIHealthStatus, ClinicalAnalysisRequest, ClinicalAnalysisResult


@runtime_checkable
class AIClinicalAnalyzer(Protocol):
    """Structural interface every AI backend must satisfy.

    All methods are async to allow non-blocking I/O against the local server.
    """

    async def analyze(
        self,
        request: ClinicalAnalysisRequest,
    ) -> ClinicalAnalysisResult:
        """Run clinical analysis and return a validated result.

        Implementations MUST:
        - Set ``requires_human_review = True`` unconditionally.
        - Set ``visual_analysis_performed = True`` only when frames were sent.
        - Not include ``evidence_frames`` in TEXT_ONLY results.
        - Write masked output to the job artifact directory.
        - Record all raw model output in a job event (not in logs).
        """
        ...

    async def health_check(self) -> AIHealthStatus:
        """Return current provider health without side effects."""
        ...

    async def get_capabilities(self) -> ModelCapabilities:
        """Return runtime-discovered capabilities for the configured model."""
        ...
