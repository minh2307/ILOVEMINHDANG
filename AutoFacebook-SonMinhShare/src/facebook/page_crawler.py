# -*- coding: utf-8 -*-
"""
src/facebook/page_crawler.py
=============================
Orchestrator: quyết định dùng Graph API hay Browser,
áp dụng dedup, ghi crawl state, trả về danh sách PostRecord.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Dict, Any

from src.common import config as cfg
from src.common.hashing import make_content_hash, is_duplicate, normalize_url
from src.common.logging_setup import get_logger
from src.facebook.post_parser import parse_post

log = get_logger("fb_crawler.page")

CRAWL_METHOD_GRAPH = "GRAPH_API"
CRAWL_METHOD_BROWSER = "BROWSER"

ERROR_CODES = {
    "INVALID_PAGE_URL",
    "ACCESS_TOKEN_MISSING",
    "ACCESS_TOKEN_EXPIRED",
    "PERMISSION_DENIED",
    "LOGIN_REQUIRED",
    "CHECKPOINT_REQUIRED",
    "RATE_LIMITED",
    "NETWORK_ERROR",
    "PARSING_ERROR",
    "EXPORT_ERROR",
}


@dataclass
class PostRecord:
    """Một bài viết đã được xử lý đầy đủ."""
    index: int = 0
    source_page: str = "robolearnai"
    post_id: str = ""
    post_url: str = ""
    created_time: str = ""
    original_heading: str = ""
    derived_title: str = ""
    content_preview: str = ""
    post_type: str = "UNKNOWN"
    has_image: bool = False
    has_video: bool = False
    external_url: str = ""
    content_hash: str = ""
    crawl_method: str = CRAWL_METHOD_GRAPH
    crawled_at: str = ""
    crawl_status: str = "SUCCESS"
    error_message: str = ""


def _load_state() -> Dict[str, Any]:
    """Đọc trạng thái crawl trước đó (resume)."""
    path = Path(cfg.CRAWL_STATE_FILE)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_post_id": "",
        "last_post_time": "",
        "last_successful_crawl": "",
        "processed_post_ids": [],
    }


def _save_state(state: Dict[str, Any]) -> None:
    """Ghi trạng thái crawl."""
    path = Path(cfg.CRAWL_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Không thể lưu crawl state: %s", exc)


class PageCrawler:
    """
    Orchestrator thu thập bài viết từ Facebook Page.
    Không gọi bất kỳ chức năng chia sẻ hay tham gia nhóm nào.
    """

    def __init__(
        self,
        page_url: str = "",
        max_posts: int = 0,
        output_csv: str = "",
        output_json: str = "",
        output_summary: str = "",
        since: str = "",
        until: str = "",
        force_browser: bool = False,
    ):
        cfg.ensure_output_dirs()
        self.page_url = page_url or cfg.FACEBOOK_SOURCE_PAGE_URL
        self.max_posts = max_posts or cfg.FACEBOOK_MAX_POSTS
        self.output_csv = output_csv or cfg.OUTPUT_CSV
        self.output_json = output_json or cfg.OUTPUT_JSON
        self.output_summary = output_summary or cfg.OUTPUT_SUMMARY
        self.since = since or cfg.FACEBOOK_CRAWL_SINCE
        self.until = until or cfg.FACEBOOK_CRAWL_UNTIL
        self.force_browser = force_browser

        self._state = _load_state()
        self._processed_ids: Set[str] = set(self._state.get("processed_post_ids", []))

    # ------------------------------------------------------------------ #
    # Public                                                               #
    # ------------------------------------------------------------------ #
    def run(self) -> "CrawlSummary":
        """
        Chạy toàn bộ quy trình:
        1. Chọn phương thức crawl.
        2. Lấy raw posts.
        3. Parse + dedup.
        4. Xuất file.
        5. Cập nhật state.
        Trả về CrawlSummary.
        """
        started_at = datetime.utcnow().isoformat() + "Z"
        log.info("=" * 60)
        log.info("🚀 Bắt đầu crawl Page: %s", self.page_url)

        # Quyết định phương thức
        use_graph = cfg.has_graph_api_config() and not self.force_browser
        crawl_method = CRAWL_METHOD_GRAPH if use_graph else CRAWL_METHOD_BROWSER
        log.info("📡 Phương thức crawl: %s", crawl_method)

        raw_posts: List[Dict[str, Any]] = []
        crawl_error = ""
        actual_method = crawl_method

        try:
            if use_graph:
                raw_posts = self._fetch_via_graph()
                actual_method = CRAWL_METHOD_GRAPH
        except Exception as exc:
            crawl_error = str(exc)
            log.error("❌ Graph API lỗi: %s", crawl_error)
            log.warning("🔄 Tự động chuyển sang Browser mode...")
            actual_method = CRAWL_METHOD_BROWSER
            crawl_method = CRAWL_METHOD_BROWSER
            try:
                raw_posts = self._fetch_via_browser()
            except Exception as exc2:
                log.error("❌ Browser cũng lỗi: %s", exc2)

        if not use_graph:
            try:
                raw_posts = self._fetch_via_browser()
                actual_method = CRAWL_METHOD_BROWSER
            except Exception as exc:
                crawl_error = str(exc)
                log.error("❌ Lỗi trong quá trình crawl: %s", crawl_error)

        log.info("📊 Số bài tìm thấy từ nguồn: %s", len(raw_posts))

        # Parse + dedup
        records, stats = self._process_posts(raw_posts, crawl_method)

        # Xuất file
        from src.exporters.csv_exporter import CSVExporter
        from src.exporters.json_exporter import JSONExporter

        completed_at = datetime.utcnow().isoformat() + "Z"

        try:
            CSVExporter(self.output_csv).export(records)
            log.info("✅ Đã xuất CSV: %s", self.output_csv)
        except Exception as exc:
            log.error("EXPORT_ERROR CSV: %s", exc)
            stats["export_error"] = str(exc)

        try:
            JSONExporter(self.output_json).export(records)
            log.info("✅ Đã xuất JSON: %s", self.output_json)
        except Exception as exc:
            log.error("EXPORT_ERROR JSON: %s", exc)

        # Tóm tắt
        summary = CrawlSummary(
            page=cfg.SOURCE_PAGE_NAME,
            total_found=len(raw_posts),
            new_posts=stats.get("new", 0),
            updated_posts=stats.get("updated", 0),
            duplicate_posts=stats.get("duplicate", 0),
            failed_posts=stats.get("failed", 0),
            crawl_method=actual_method,
            started_at=started_at,
            completed_at=completed_at,
        )

        try:
            JSONExporter(self.output_summary).export_raw(summary.to_dict())
            log.info("✅ Đã xuất summary: %s", self.output_summary)
        except Exception as exc:
            log.error("EXPORT_ERROR summary: %s", exc)

        # Cập nhật state
        self._update_state(records)

        log.info("=" * 60)
        log.info("🎉 Hoàn tất crawl Page")
        log.info("   Bài mới      : %s", summary.new_posts)
        log.info("   Bài trùng    : %s", summary.duplicate_posts)
        log.info("   Bài lỗi      : %s", summary.failed_posts)
        log.info("   CSV          : %s", self.output_csv)
        log.info("=" * 60)

        return summary

    # ------------------------------------------------------------------ #
    # Fetch                                                                #
    # ------------------------------------------------------------------ #
    def _fetch_via_graph(self) -> List[Dict[str, Any]]:
        from src.facebook.graph_client import GraphClient, GraphAPIError
        try:
            client = GraphClient(
                max_posts=self.max_posts,
                since=self.since,
                until=self.until,
            )
            return client.fetch_posts()
        except GraphAPIError as exc:
            log.error("GraphAPIError: %s", exc)
            raise

    def _fetch_via_browser(self) -> List[Dict[str, Any]]:
        from src.facebook.browser_client import BrowserClient, BrowserCrawlError
        try:
            client = BrowserClient(
                page_url=self.page_url,
                max_posts=self.max_posts,
            )
            return client.fetch_posts()
        except BrowserCrawlError as exc:
            log.error("BrowserCrawlError: %s", exc)
            raise

    # ------------------------------------------------------------------ #
    # Process                                                              #
    # ------------------------------------------------------------------ #
    def _process_posts(
        self, raw_posts: List[Dict[str, Any]], crawl_method: str
    ):
        """Parse, dedup và trả về (records, stats_dict)."""
        # Tập dedup từ bài cũ
        known_ids: Set[str] = set(self._processed_ids)
        known_urls: Set[str] = set()
        known_hashes: Set[str] = set()

        # Đọc CSV cũ để nạp known_urls / known_hashes
        self._load_existing_dedup_sets(known_ids, known_urls, known_hashes)

        records: List[PostRecord] = []
        stats = {"new": 0, "updated": 0, "duplicate": 0, "failed": 0}
        crawled_at = datetime.utcnow().isoformat() + "Z"

        for raw in raw_posts:
            try:
                raw["crawl_method"] = crawl_method
                raw["crawled_at"] = crawled_at
                parsed = parse_post(raw, cfg.SOURCE_PAGE_NAME)

                post_id = parsed.get("post_id") or ""
                post_url = normalize_url(parsed.get("post_url") or "")
                content_hash = make_content_hash(
                    parsed.get("original_heading", ""),
                    parsed.get("content_preview", ""),
                    parsed.get("created_time", ""),
                )

                if is_duplicate(post_id, post_url, content_hash, known_ids, known_urls, known_hashes):
                    stats["duplicate"] += 1
                    log.debug("⚠️  Bài trùng bỏ qua: %s", post_id or post_url)
                    continue

                # Đánh dấu đã xử lý
                if post_id:
                    known_ids.add(post_id)
                if post_url:
                    known_urls.add(post_url)
                known_hashes.add(content_hash)

                rec = PostRecord(
                    index=len(records) + 1,
                    source_page=parsed.get("source_page", cfg.SOURCE_PAGE_NAME),
                    post_id=post_id,
                    post_url=post_url,
                    created_time=parsed.get("created_time", ""),
                    original_heading=parsed.get("original_heading", ""),
                    derived_title=parsed.get("derived_title", ""),
                    content_preview=parsed.get("content_preview", ""),
                    post_type=parsed.get("post_type", "UNKNOWN"),
                    has_image=bool(parsed.get("has_image")),
                    has_video=bool(parsed.get("has_video")),
                    external_url=parsed.get("external_url", ""),
                    content_hash=content_hash,
                    crawl_method=crawl_method,
                    crawled_at=crawled_at,
                    crawl_status=parsed.get("crawl_status", "SUCCESS"),
                    error_message=parsed.get("error_message", ""),
                )
                records.append(rec)
                stats["new"] += 1

            except Exception as exc:
                log.warning("PARSING_ERROR bài %s: %s", raw.get("id", "?"), exc)
                stats["failed"] += 1

        # Sắp xếp cũ → mới theo created_time
        records = self._sort_records(records)
        # Cập nhật lại index sau sắp xếp
        for i, r in enumerate(records, 1):
            r.index = i

        return records, stats

    def _sort_records(self, records: List[PostRecord]) -> List[PostRecord]:
        """Sắp xếp bài cũ → mới; bài không có ngày → cuối."""
        def sort_key(r: PostRecord):
            if r.created_time:
                return (0, r.created_time)
            return (1, "")
        return sorted(records, key=sort_key)

    def _load_existing_dedup_sets(
        self, known_ids: Set[str], known_urls: Set[str], known_hashes: Set[str]
    ) -> None:
        """Đọc CSV cũ để nạp known sets (tránh xuất bài trùng từ lần trước)."""
        import csv as _csv
        path = Path(self.output_csv)
        if not path.exists():
            return
        try:
            with path.open(encoding="utf-8-sig") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    pid = (row.get("post_id") or "").strip()
                    purl = normalize_url((row.get("post_url") or "").strip())
                    phash = (row.get("content_hash") or "").strip()
                    if pid:
                        known_ids.add(pid)
                    if purl:
                        known_urls.add(purl)
                    if phash:
                        known_hashes.add(phash)
            log.info("Đã nạp dedup từ CSV cũ: %s ids, %s urls, %s hashes",
                     len(known_ids), len(known_urls), len(known_hashes))
        except Exception as exc:
            log.warning("Không thể đọc CSV cũ để dedup: %s", exc)

    def _update_state(self, records: List[PostRecord]) -> None:
        """Cập nhật crawl_state sau khi crawl xong."""
        state = _load_state()
        existing_ids = set(state.get("processed_post_ids", []))
        for r in records:
            if r.post_id:
                existing_ids.add(r.post_id)
        if records:
            # Bài mới nhất (cuối danh sách sau sort)
            latest = records[-1]
            state["last_post_id"] = latest.post_id
            state["last_post_time"] = latest.created_time
        state["last_successful_crawl"] = datetime.utcnow().isoformat() + "Z"
        state["processed_post_ids"] = list(existing_ids)
        _save_state(state)


@dataclass
class CrawlSummary:
    page: str = ""
    total_found: int = 0
    new_posts: int = 0
    updated_posts: int = 0
    duplicate_posts: int = 0
    failed_posts: int = 0
    crawl_method: str = ""
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page,
            "total_found": self.total_found,
            "new_posts": self.new_posts,
            "updated_posts": self.updated_posts,
            "duplicate_posts": self.duplicate_posts,
            "failed_posts": self.failed_posts,
            "crawl_method": self.crawl_method,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
