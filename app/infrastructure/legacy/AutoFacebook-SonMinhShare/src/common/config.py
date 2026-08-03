# -*- coding: utf-8 -*-
"""
src/common/config.py
====================
Trung tâm đọc cấu hình cho module crawler Page Facebook.
Các biến ENV dùng tiền tố FB_CRAWL_ hoặc FACEBOOK_* để
KHÔNG đụng vào các biến cũ của visit-like-post.py
(HEADLESS, TASK4JOIN_CSV, OPENAI_API_KEY, …).
"""

import os
from pathlib import Path

try:
    from dotenv import dotenv_values

    # Legacy compatibility only: read values without mutating the process-wide
    # environment used by the official Settings composition root.
    _DOTENV_VALUES = dotenv_values()
except Exception:
    _DOTENV_VALUES = {}


def _env(key: str, default=None) -> str:
    v = os.getenv(key, _DOTENV_VALUES.get(key))
    return v if (v is not None and str(v).strip() != "") else default


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def _env_int(key: str, default: int = 0) -> int:
    v = _env(key)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_float(key: str, default: float = 0.0) -> float:
    v = _env(key)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


# ======= Meta Graph API (phương án ưu tiên) =======
FACEBOOK_PAGE_ID: str = _env("FACEBOOK_PAGE_ID", "")
# KHÔNG in ra log — chỉ đọc nội bộ
FACEBOOK_ACCESS_TOKEN: str = _env("FACEBOOK_ACCESS_TOKEN", "")
FACEBOOK_GRAPH_API_VERSION: str = _env("FACEBOOK_GRAPH_API_VERSION", "v21.0")

# ======= Nguồn Page cần crawl =======
FACEBOOK_SOURCE_PAGE_URL: str = _env(
    "FACEBOOK_SOURCE_PAGE_URL",
    "https://www.facebook.com/robolearnai"
)
FACEBOOK_MAX_POSTS: int = _env_int("FACEBOOK_MAX_POSTS", 100)
FACEBOOK_CRAWL_SINCE: str = _env("FACEBOOK_CRAWL_SINCE", "")   # ISO date, bỏ trống = không giới hạn
FACEBOOK_CRAWL_UNTIL: str = _env("FACEBOOK_CRAWL_UNTIL", "")   # ISO date

# ======= Browser automation (phương án dự phòng) =======
# Dùng biến riêng để KHÔNG ghi đè HEADLESS của chức năng cũ
FACEBOOK_HEADLESS: bool = _env_bool("FACEBOOK_HEADLESS", True)
FACEBOOK_SCROLL_LIMIT: int = _env_int("FACEBOOK_SCROLL_LIMIT", 50)
FACEBOOK_SCROLL_DELAY_SECONDS: float = _env_float("FACEBOOK_SCROLL_DELAY_SECONDS", 2.0)
FACEBOOK_REQUEST_DELAY_SECONDS: float = _env_float("FACEBOOK_REQUEST_DELAY_SECONDS", 2.0)

# ======= Output =======
OUTPUT_DIRECTORY: str = _env("OUTPUT_DIRECTORY", "output")
OUTPUT_CSV: str = _env("OUTPUT_CSV", f"{OUTPUT_DIRECTORY}/robolearnai_titles.csv")
OUTPUT_JSON: str = _env("OUTPUT_JSON", f"{OUTPUT_DIRECTORY}/robolearnai_posts.json")
OUTPUT_SUMMARY: str = _env("OUTPUT_SUMMARY", f"{OUTPUT_DIRECTORY}/robolearnai_crawl_summary.json")

# ======= Trạng thái resume =======
CRAWL_STATE_DIR: str = "data/crawl_state"
CRAWL_STATE_FILE: str = f"{CRAWL_STATE_DIR}/robolearnai.json"

# ======= Tên page nguồn (cố định cho module này) =======
SOURCE_PAGE_NAME: str = "robolearnai"


def ensure_output_dirs() -> None:
    """Tạo thư mục output và crawl_state nếu chưa tồn tại."""
    Path(OUTPUT_DIRECTORY).mkdir(parents=True, exist_ok=True)
    Path(CRAWL_STATE_DIR).mkdir(parents=True, exist_ok=True)


def has_graph_api_config() -> bool:
    """Trả True nếu đủ thông tin để gọi Graph API."""
    return bool(FACEBOOK_ACCESS_TOKEN and FACEBOOK_PAGE_ID)
