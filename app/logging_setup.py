from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from app.config.settings import Settings
from app.services.privacy_service import PrivacyService

_STANDARD_FIELDS = set(logging.makeLogRecord({}).__dict__)
_SENSITIVE_KEYS = {"authorization", "password", "passwd", "token", "access_token", "api_key", "cookie", "cookies", "secret"}
_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)\b(password|passwd|token|access_token|api_key|cookie|secret)\s*[:=]\s*[^\s,;]+"),
)
_PRIVACY = PrivacyService()


def mask_sensitive(value: str) -> str:
    masked = str(value)
    for pattern in _SENSITIVE_PATTERNS:
        masked = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", masked)
    return _PRIVACY.mask(masked)


def _redact_value(value: Any, *, key: str = "") -> Any:
    if key.casefold() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, str):
        return mask_sensitive(value)
    if isinstance(value, dict):
        return {str(item_key): _redact_value(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return mask_sensitive(str(value))


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_sensitive(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = _redact_value(value, key=key)
        if record.exc_info:
            payload["exception"] = mask_sensitive(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings) -> logging.Logger:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("cdha_pipeline")
    logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = StructuredJsonFormatter()
    file_handler = RotatingFileHandler(
        settings.log_dir / "workflow.jsonl",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"cdha_pipeline.{name}")
