# -*- coding: utf-8 -*-
"""
src/common/hashing.py
======================
Hàm tạo content_hash (SHA-256) và chuẩn hóa URL/text để chống trùng.
Giữ nguyên dấu tiếng Việt, không xóa tên sản phẩm.
"""

import hashlib
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from typing import Optional


# -------- Các query param theo dõi cần loại bỏ --------
_TRACKING_PARAMS = {
    "mibextid", "rdid", "share_url", "fbclid", "utm_source",
    "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "__cft__", "__tn__", "ref", "refid",
}


def normalize_url(url: str) -> str:
    """
    Chuẩn hóa URL: xóa tracking params, không đổi path.
    Ví dụ: 'https://fb.com/post/123?mibextid=abc' → 'https://fb.com/post/123'
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
        new_query = urlencode(filtered, doseq=True)
        clean = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            ""  # fragment bỏ
        ))
        return clean.rstrip("?")
    except Exception:
        return url


def normalize_text(text: str) -> str:
    """
    Chuẩn hóa text để hash:
    - Chuyển về chữ thường.
    - Loại bỏ khoảng trắng dư thừa.
    - Giữ nguyên dấu tiếng Việt.
    """
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_content_hash(
    original_heading: str,
    content_preview: str,
    created_time: str,
) -> str:
    """
    Tạo SHA-256 từ tổ hợp:
        normalize(original_heading) + normalize(content_preview) + normalize(created_time)
    """
    parts = [
        normalize_text(original_heading or ""),
        normalize_text(content_preview or ""),
        normalize_text(created_time or ""),
    ]
    combined = "|".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def is_duplicate(
    post_id: Optional[str],
    post_url: Optional[str],
    content_hash: Optional[str],
    known_ids: set,
    known_urls: set,
    known_hashes: set,
) -> bool:
    """
    Kiểm tra bài viết có bị trùng không theo thứ tự ưu tiên:
    1. post_id
    2. post_url (đã normalize)
    3. content_hash
    """
    if post_id and post_id in known_ids:
        return True
    clean_url = normalize_url(post_url or "")
    if clean_url and clean_url in known_urls:
        return True
    if content_hash and content_hash in known_hashes:
        return True
    return False
