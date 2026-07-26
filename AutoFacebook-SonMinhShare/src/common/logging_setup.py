# -*- coding: utf-8 -*-
"""
src/common/logging_setup.py
============================
Thiết lập logging với bộ lọc ẩn token, cookie, password.
Không ghi đè logging của visit-like-post.py (dùng print).
"""

import logging
import re
from typing import List


# --------- Bộ lọc che thông tin nhạy cảm ---------
_SENSITIVE_PATTERNS: List[re.Pattern] = [
    re.compile(r"(access_token=)[^\s&\"']+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(token[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-]{10,}", re.IGNORECASE),
    re.compile(r"(password[\"']?\s*[:=]\s*[\"']?)\S+", re.IGNORECASE),
    re.compile(r"(cookie[s]?[\"']?\s*[:=]\s*[\"']?)\S{20,}", re.IGNORECASE),
    re.compile(r"(xs=)[^\s;\"']+", re.IGNORECASE),
    re.compile(r"(c_user=)[^\s;\"']+", re.IGNORECASE),
    re.compile(r"(datr=)[^\s;\"']+", re.IGNORECASE),
]


def mask_sensitive(text: str) -> str:
    """Che phủ thông tin nhạy cảm trong chuỗi text."""
    for pat in _SENSITIVE_PATTERNS:
        text = pat.sub(r"\1[MASKED]", text)
    return text


class SensitiveFilter(logging.Filter):
    """Filter tự động ẩn sensitive data trong mọi log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = mask_sensitive(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: mask_sensitive(str(v)) for k, v in record.args.items()}
                else:
                    record.args = tuple(mask_sensitive(str(a)) for a in record.args)
        except Exception:
            pass
        return True


def get_logger(name: str = "fb_crawler") -> logging.Logger:
    """
    Trả về logger đã được thiết lập với:
    - Handler console (INFO+)
    - Bộ lọc che token/cookie
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        # Tránh thêm handler trùng khi import nhiều lần
        return logger

    logger.setLevel(logging.DEBUG)

    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    handler.addFilter(SensitiveFilter())

    logger.addFilter(SensitiveFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
