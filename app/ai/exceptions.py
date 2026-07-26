"""Typed exceptions for the AI provider layer.

All exceptions are subclasses of AIProviderError so callers can catch the
entire AI layer with a single except clause when needed.

Hierarchy
---------
AIProviderError
├── ModelCapabilityError   — model lacks a required capability (e.g. vision)
├── AITimeoutError         — request exceeded configured timeout
├── AIConnectionError      — cannot reach Ollama or provider
└── AIOutputValidationError — model response failed schema / safety checks
"""
from __future__ import annotations


class AIProviderError(RuntimeError):
    """Base class for all AI provider errors."""


class ModelCapabilityError(AIProviderError):
    """Raised when the configured model lacks a required capability.

    For example: requesting vision analysis from a text-only model.
    """


class AITimeoutError(AIProviderError):
    """Raised when an AI request exceeds its deadline."""


class AIConnectionError(AIProviderError):
    """Raised when the AI provider (Ollama) is unreachable."""


class AIOutputValidationError(AIProviderError):
    """Raised when the model output fails schema or safety validation."""
