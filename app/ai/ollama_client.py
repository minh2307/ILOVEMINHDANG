"""Ollama HTTP client — thin async wrapper around the local Ollama REST API.

Design rules
------------
* No model name is hard-coded; every call uses settings.ollama_model.
* Raw prompts, base-64 image data and raw responses are NEVER written to logs.
* Only one request is in-flight per client instance (no connection pool needed
  for a local service).
* Retry is applied only to connection/network errors where the outcome is
  definitively unknown-nothing.  It is NOT applied when a request was sent and
  the outcome is unclear (e.g. timeout after sending).
* Model pull is never initiated automatically; Operator must pull beforehand.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.ai.capability import ModelCapabilities
from app.ai.exceptions import (
    AIConnectionError,
    AIProviderError,
    AITimeoutError,
)
from app.ai.models import AIHealthStatus


class OllamaClient:
    """Low-level async client for the Ollama local REST API.

    Parameters
    ----------
    base_url:
        Ollama server URL, e.g. ``http://localhost:11434``.
    model:
        Model tag to use for all requests.
    timeout_seconds:
        Per-request deadline in seconds.
    keep_alive:
        Ollama ``keep_alive`` parameter (e.g. ``"10m"``).
    temperature:
        Sampling temperature (0.0 = deterministic).
    max_retries:
        Number of retry attempts for transient connection errors *before* the
        request has been sent.  Never retried after request is in-flight.
    logger:
        Optional logger; defaults to ``cdha_pipeline.ollama_client``.
    """

    _TAG = "ollama_client"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str,
        timeout_seconds: int = 300,
        keep_alive: str = "10m",
        temperature: float = 0.1,
        max_retries: int = 2,
        logger: logging.Logger | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._keep_alive = keep_alive
        self._temperature = temperature
        self._max_retries = max_retries
        self._logger = logger or logging.getLogger("cdha_pipeline.ollama_client")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def health_check(self) -> AIHealthStatus:
        """Return health status without touching the model."""
        from datetime import UTC, datetime

        checked_at = datetime.now(UTC).isoformat()
        try:
            response = await self._get("/api/tags", timeout=5)
            models = [m.get("name", "") for m in response.get("models", [])]
            model_loaded = any(
                name == self._model or name.startswith(self._model + ":")
                for name in models
            )
            if not model_loaded:
                return AIHealthStatus(
                    healthy=False,
                    provider="ollama",
                    model=self._model,
                    detail=f"Model '{self._model}' not found in Ollama. Available: {models}",
                    checked_at=checked_at,
                )
            return AIHealthStatus(
                healthy=True,
                provider="ollama",
                model=self._model,
                detail="ok",
                checked_at=checked_at,
            )
        except AIConnectionError as exc:
            return AIHealthStatus(
                healthy=False,
                provider="ollama",
                model=self._model,
                detail=str(exc),
                checked_at=checked_at,
            )

    async def list_models(self) -> tuple[str, ...]:
        """Return configured server model identifiers without pulling a model."""
        response = await self._get("/api/tags", timeout=min(15, self._timeout))
        models = response.get("models", [])
        if not isinstance(models, list):
            raise AIProviderError("Ollama /api/tags returned an invalid models payload")
        return tuple(
            str(item.get("name", "")).strip()
            for item in models
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        )

    async def get_capabilities(self) -> ModelCapabilities:
        """Determine model capabilities from Ollama model info API.

        Falls back to ``heuristic`` source when the API does not expose enough
        information.  Vision support is inferred from the presence of
        ``"vision"`` or ``"multimodal"`` in the model metadata, NOT from the
        model name alone.
        """
        try:
            info = await self._post(
                "/api/show",
                {"name": self._model},
                timeout=15,
                retries=1,
            )
        except AIProviderError:
            self._logger.warning(
                "Could not fetch model info from Ollama; using text-only fallback",
                extra={"model": self._model},
            )
            return ModelCapabilities.text_only(source="api_unavailable")

        details = info.get("details", {})
        modelinfo = info.get("modelinfo", {})
        families = details.get("families", []) or []
        families_lower = [f.casefold() for f in families]

        # Ollama >= 0.3 exposes projector_info for vision models
        has_projector = bool(info.get("projector_info") or modelinfo.get("clip.has_vision_encoder"))
        has_vision_family = any(
            kw in f for f in families_lower for kw in ("vision", "llava", "qwen2-vl", "minicpm-v")
        )
        supports_images = has_projector or has_vision_family

        context_length: int | None = None
        for key in ("llama.context_length", "context_length"):
            val = modelinfo.get(key)
            if isinstance(val, int):
                context_length = val
                break

        source = "ollama_api_modelinfo"
        return ModelCapabilities(
            supports_text=True,
            supports_images=supports_images,
            supports_video=False,
            supports_json_schema=False,
            context_length=context_length,
            source=source,
        )

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        stream: bool = False,
    ) -> str:
        """Send a chat request and return the assistant text content.

        Images in messages must already be base-64 encoded by the caller.
        This method does NOT log message content.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": stream,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": self._temperature,
                "num_ctx": 8192
            },
        }
        response = await self._post(
            "/api/chat",
            payload,
            timeout=self._timeout,
            retries=0,  # Never retry after request is sent
        )
        return response.get("message", {}).get("content", "")

    async def generate(
        self,
        *,
        prompt: str,
        images: list[str] | None = None,
        stream: bool = False,
    ) -> str:
        """Send a generate request (single-turn) and return the response.

        ``images`` is a list of base-64 encoded JPEG/PNG strings.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": stream,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": self._temperature,
                "num_ctx": 8192
            },
        }
        if images:
            payload["images"] = images
        response = await self._post(
            "/api/generate",
            payload,
            timeout=self._timeout,
            retries=0,
        )
        return response.get("response", "")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def encode_image(path: Path) -> str:
        """Return base-64 encoded image bytes from a file path."""
        return base64.b64encode(path.read_bytes()).decode("ascii")

    # ------------------------------------------------------------------
    # Low-level HTTP (sync urllib in executor to stay in asyncio)
    # ------------------------------------------------------------------

    async def _get(self, endpoint: str, *, timeout: int = 10) -> dict[str, Any]:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._sync_get, endpoint, timeout
        )

    def _sync_get(self, endpoint: str, timeout: int) -> dict[str, Any]:
        url = f"{self._base_url}{endpoint}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise AIConnectionError(f"Cannot reach Ollama at {url}: {exc}") from exc
        except TimeoutError as exc:
            raise AITimeoutError(f"Ollama GET {url} timed out") from exc

    async def _post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        timeout: int,
        retries: int,
    ) -> dict[str, Any]:
        attempt = 0
        last_exc: Exception | None = None
        while attempt <= retries:
            try:
                return await asyncio.get_event_loop().run_in_executor(
                    None, self._sync_post, endpoint, payload, timeout
                )
            except AIConnectionError as exc:
                last_exc = exc
                attempt += 1
                if attempt <= retries:
                    await asyncio.sleep(min(2.0 ** attempt, 8.0))
            except (AITimeoutError, AIProviderError):
                raise
        raise last_exc  # type: ignore[misc]

    def _sync_post(
        self, endpoint: str, payload: dict[str, Any], timeout: int
    ) -> dict[str, Any]:
        url = f"{self._base_url}{endpoint}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                # Ollama may return NDJSON for streaming; take last object
                lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
                if not lines:
                    raise AIProviderError("Empty response from Ollama")
                return json.loads(lines[-1])
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise AIProviderError(
                f"Ollama HTTP {exc.code} at {endpoint}: {body_text[:200]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AIConnectionError(f"Cannot reach Ollama at {url}: {exc}") from exc
        except TimeoutError as exc:
            raise AITimeoutError(f"Ollama POST {url} timed out after {timeout}s") from exc
