"""Factory — constructs the correct AIClinicalAnalyzer from Settings.

Usage
-----
    from app.ai.provider_factory import build_analyzer
    analyzer = build_analyzer(settings, job_data_dir=settings.job_data_dir)

Only ``OllamaAnalyzer`` is implemented for now.  Future providers (e.g. a
local OpenAI-compatible endpoint) would be added here without touching the
pipeline.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.ai.ollama_analyzer import OllamaAnalyzer
from app.ai.ollama_client import OllamaClient
from app.ai.protocols import AIClinicalAnalyzer
from app.config.settings import Settings


def build_analyzer(
    settings: Settings,
    *,
    job_data_dir: Path | None = None,
    logger: logging.Logger | None = None,
) -> AIClinicalAnalyzer:
    """Return a fully configured AIClinicalAnalyzer for the given settings.

    Currently only ``ollama`` provider is supported.
    """
    client = OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        keep_alive=settings.ollama_keep_alive,
        temperature=settings.ollama_temperature,
        max_retries=settings.ollama_max_retries,
        logger=logger,
    )
    return OllamaAnalyzer(
        client,
        job_data_dir=job_data_dir or settings.job_data_dir,
        save_raw_response=settings.save_raw_ollama_response,
        max_comment_chars=settings.clinical_factors_comment_max_chars,
        max_comments=settings.clinical_factors_max_comments,
        max_total_comment_chars=settings.ollama_comment_total_max_chars,
        max_prompt_chars=settings.ollama_prompt_max_chars,
        max_response_chars=settings.clinical_factors_max_chars,
        logger=logger,
    )
