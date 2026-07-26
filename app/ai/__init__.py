"""AI provider abstraction layer.

This package provides a provider-agnostic interface for clinical AI analysis,
allowing the pipeline to switch between Ollama local models without changing
upstream orchestration logic.

Public surface
--------------
- protocols.AIClinicalAnalyzer   — the interface pipeline depends on
- models.*                       — request / response value objects
- capability.ModelCapabilities   — runtime capability descriptor
- exceptions.*                   — typed errors for AI layer
- provider_factory.build_analyzer — factory used by CDHAPipeline
"""
from app.ai.protocols import AIClinicalAnalyzer
from app.ai.models import ClinicalAnalysisRequest, ClinicalAnalysisResult, AIHealthStatus
from app.ai.capability import ModelCapabilities
from app.ai.exceptions import (
    AIProviderError,
    ModelCapabilityError,
    AITimeoutError,
    AIConnectionError,
    AIOutputValidationError,
)

__all__ = [
    "AIClinicalAnalyzer",
    "ClinicalAnalysisRequest",
    "ClinicalAnalysisResult",
    "AIHealthStatus",
    "ModelCapabilities",
    "AIProviderError",
    "ModelCapabilityError",
    "AITimeoutError",
    "AIConnectionError",
    "AIOutputValidationError",
]
