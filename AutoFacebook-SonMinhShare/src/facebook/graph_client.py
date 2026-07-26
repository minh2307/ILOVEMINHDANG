# -*- coding: utf-8 -*-
"""
src/facebook/graph_client.py
=============================
Client gọi Meta Graph API để lấy bài viết từ một Facebook Page.
Access token KHÔNG bao giờ được in ra log.

Quyền cần thiết:
  pages_read_engagement  (hoặc read_stream cho user token)
  pages_show_list

Biến môi trường cần đặt (xem src/common/config.py):
  FACEBOOK_PAGE_ID
  FACEBOOK_ACCESS_TOKEN
  FACEBOOK_GRAPH_API_VERSION
"""

import time
from datetime import datetime
from typing import Dict, Any, List, Optional

import requests

from src.common import config as cfg
from src.common.logging_setup import get_logger

log = get_logger("fb_crawler.graph")

# -------- Hằng số --------
GRAPH_BASE = "https://graph.facebook.com"
FIELDS = (
    "id,message,created_time,permalink_url,"
    "attachments,status_type,full_picture"
)
MAX_RETRIES = 3
RETRY_WAIT = 5  # giây


class GraphAPIError(Exception):
    """Lỗi từ Meta Graph API."""

    def __init__(self, code: int, message: str, error_type: str = ""):
        self.code = code
        self.message = message
        self.error_type = error_type
        super().__init__(f"[{code}] {message}")


def _call(endpoint: str, params: Dict[str, Any], retry: int = MAX_RETRIES) -> Dict:
    """
    Gọi GET đến Graph API với cơ chế retry.
    Không log access_token.
    """
    # Thêm token mà không log
    params = dict(params)
    params["access_token"] = cfg.FACEBOOK_ACCESS_TOKEN

    url = f"{GRAPH_BASE}/{cfg.FACEBOOK_GRAPH_API_VERSION}/{endpoint}"

    for attempt in range(1, retry + 1):
        try:
            resp = requests.get(url, params=params, timeout=20)
        except requests.RequestException as exc:
            log.warning("NETWORK_ERROR attempt %d/%d: %s", attempt, retry, type(exc).__name__)
            if attempt == retry:
                raise GraphAPIError(0, f"NETWORK_ERROR: {exc}") from exc
            time.sleep(RETRY_WAIT * attempt)
            continue

        if resp.status_code == 429:
            wait = RETRY_WAIT * attempt * 2
            log.warning("RATE_LIMITED — chờ %ds (attempt %d/%d)", wait, attempt, retry)
            time.sleep(wait)
            continue

        if not resp.ok:
            try:
                err = resp.json().get("error", {})
                code = err.get("code", resp.status_code)
                msg = err.get("message", resp.text[:200])
                etype = err.get("type", "")
            except Exception:
                code, msg, etype = resp.status_code, resp.text[:200], ""
            raise GraphAPIError(code, msg, etype)

        return resp.json()

    raise GraphAPIError(0, "Max retries exceeded")


class GraphClient:
    """Client thu thập bài viết từ Page qua Meta Graph API."""

    def __init__(
        self,
        page_id: str = "",
        max_posts: int = 100,
        since: str = "",
        until: str = "",
    ):
        self.page_id = page_id or cfg.FACEBOOK_PAGE_ID
        self.max_posts = max_posts or cfg.FACEBOOK_MAX_POSTS
        self.since = since or cfg.FACEBOOK_CRAWL_SINCE
        self.until = until or cfg.FACEBOOK_CRAWL_UNTIL

        if not self.page_id:
            raise GraphAPIError(0, "FACEBOOK_PAGE_ID chưa được đặt.", "CONFIG_ERROR")
        if not cfg.FACEBOOK_ACCESS_TOKEN:
            raise GraphAPIError(0, "ACCESS_TOKEN_MISSING", "CONFIG_ERROR")

    def _build_feed_params(self, after_cursor: str = "") -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "fields": FIELDS,
            "limit": min(self.max_posts, 100),
        }
        if self.since:
            params["since"] = self.since
        if self.until:
            params["until"] = self.until
        if after_cursor:
            params["after"] = after_cursor
        return params

    def fetch_posts(self) -> List[Dict[str, Any]]:
        """
        Lấy tối đa `max_posts` bài từ feed của Page.
        Trả về list[dict] chứa dữ liệu thô từ API.
        Ném GraphAPIError nếu API trả lỗi.
        """
        # Kiểm tra token trước khi gọi feed
        if not self.verify_token():
            raise GraphAPIError(
                190,
                "ACCESS_TOKEN_INVALID: Token không hợp lệ hoặc đã hết hạn.\n"
                "→ Lấy token mới tại: https://developers.facebook.com/tools/explorer/\n"
                "→ Qüyền cần: pages_read_engagement, pages_show_list",
                "OAuthException",
            )

        log.info("📡 Bắt đầu gọi Graph API cho page_id=%s", self.page_id)
        posts: List[Dict[str, Any]] = []
        after_cursor: str = ""
        crawled_at = datetime.utcnow().isoformat() + "Z"

        while len(posts) < self.max_posts:
            params = self._build_feed_params(after_cursor)
            data = _call(f"{self.page_id}/feed", params)

            items = data.get("data", [])
            if not items:
                log.info("Không còn bài viết nào từ API.")
                break

            for item in items:
                if len(posts) >= self.max_posts:
                    break
                item["crawl_method"] = "GRAPH_API"
                item["crawled_at"] = crawled_at
                item["crawl_status"] = "SUCCESS"
                item["error_message"] = ""
                # Phát hiện video sơ bộ
                item["has_video"] = self._has_video(item)
                posts.append(item)

            log.info("Đã lấy %s/%s bài viết…", len(posts), self.max_posts)

            # Phân trang
            paging = data.get("paging", {})
            cursors = paging.get("cursors", {})
            after_cursor = cursors.get("after", "")
            if not after_cursor or not paging.get("next"):
                break

            time.sleep(cfg.FACEBOOK_REQUEST_DELAY_SECONDS)

        log.info("✅ Graph API hoàn tất: %s bài viết", len(posts))
        return posts

    @staticmethod
    def _has_video(item: Dict) -> bool:
        """Kiểm tra sơ bộ xem bài có video không."""
        attachments = item.get("attachments", {})
        for att in attachments.get("data", []):
            t = att.get("type", "")
            if "video" in t:
                return True
        return False

    def verify_token(self) -> bool:
        """Kiểm tra token còn hợp lệ không (debug_token endpoint)."""
        try:
            # Gọi /me để kiểm tra token cơ bản
            data = _call("me", {"fields": "id,name"})
            log.info("Token hợp lệ, user/page: %s", data.get("name") or data.get("id"))
            return True
        except GraphAPIError as e:
            log.error("Token không hợp lệ: %s", e.message)
            return False
