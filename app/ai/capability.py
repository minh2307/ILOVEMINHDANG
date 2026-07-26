"""ModelCapabilities — runtime descriptor for AI model capabilities.

The capability is always discovered at runtime and never inferred solely from
a model name.  The ``source`` field records how it was determined so callers
can reason about reliability.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Immutable descriptor of what a specific model instance can do.

    Fields
    ------
    supports_text : bool
        Model accepts and generates plain text (always True for useful models).
    supports_images : bool
        Model accepts base-64 encoded image data (multimodal / vision).
    supports_video : bool
        Model can natively process raw video bytes (rare; Ollama does not
        currently expose this).  Distinct from sending extracted frames.
    supports_json_schema : bool
        Model accepts a JSON schema and returns schema-conformant output.
    context_length : int | None
        Maximum context window in tokens, if known.
    source : str
        How capability was determined, e.g.
        "ollama_api_modelinfo", "config_override", "heuristic".
    """

    supports_text: bool
    supports_images: bool
    supports_video: bool
    supports_json_schema: bool
    context_length: int | None
    source: str

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def text_only(cls, *, context_length: int | None = None, source: str = "unknown") -> "ModelCapabilities":
        """Return a capabilities descriptor for a text-only model."""
        return cls(
            supports_text=True,
            supports_images=False,
            supports_video=False,
            supports_json_schema=False,
            context_length=context_length,
            source=source,
        )

    @classmethod
    def vision(cls, *, context_length: int | None = None, source: str = "unknown") -> "ModelCapabilities":
        """Return a capabilities descriptor for a multimodal (vision) model."""
        return cls(
            supports_text=True,
            supports_images=True,
            supports_video=False,
            supports_json_schema=False,
            context_length=context_length,
            source=source,
        )
