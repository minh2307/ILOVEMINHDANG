# -*- coding: utf-8 -*-
"""
src/exporters/csv_exporter.py
==============================
Xuất danh sách PostRecord ra file CSV.
- Encoding UTF-8 BOM (đọc đúng tiếng Việt trong Excel).
- Không ghi đè dòng người dùng đã chỉnh sửa (giữ bài cũ, thêm bài mới).
- Sắp xếp theo created_time (cũ → mới); bài không có ngày → cuối.
- Không xuất dòng trùng.
"""

import csv
import io
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import List, Dict, Optional

from src.common.logging_setup import get_logger

log = get_logger("fb_crawler.csv_exporter")

# Thứ tự cột trong CSV (đúng với yêu cầu)
CSV_COLUMNS = [
    "index",
    "source_page",
    "post_id",
    "post_url",
    "created_time",
    "original_heading",
    "derived_title",
    "content_preview",
    "post_type",
    "has_image",
    "has_video",
    "external_url",
    "content_hash",
    "crawl_method",
    "crawled_at",
    "crawl_status",
    "error_message",
]


def _record_to_row(rec) -> Dict[str, str]:
    """Chuyển PostRecord thành dict string-safe."""
    return {
        "index": str(rec.index),
        "source_page": str(rec.source_page or ""),
        "post_id": str(rec.post_id or ""),
        "post_url": str(rec.post_url or ""),
        "created_time": str(rec.created_time or ""),
        "original_heading": str(rec.original_heading or ""),
        "derived_title": str(rec.derived_title or ""),
        "content_preview": str(rec.content_preview or ""),
        "post_type": str(rec.post_type or "UNKNOWN"),
        "has_image": "true" if rec.has_image else "false",
        "has_video": "true" if rec.has_video else "false",
        "external_url": str(rec.external_url or ""),
        "content_hash": str(rec.content_hash or ""),
        "crawl_method": str(rec.crawl_method or ""),
        "crawled_at": str(rec.crawled_at or ""),
        "crawl_status": str(rec.crawl_status or "SUCCESS"),
        "error_message": str(rec.error_message or ""),
    }


class CSVExporter:
    """Xuất / cập nhật file CSV."""

    def __init__(self, output_path: str):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, new_records: list) -> None:
        """
        Kết hợp bài cũ (từ file CSV hiện tại) với bài mới.
        Dedup theo post_id hoặc content_hash.
        Ghi lại toàn bộ file với UTF-8 BOM.
        """
        existing_rows = self._read_existing()

        # Tập ID và hash từ bài cũ
        existing_ids: set = set()
        existing_hashes: set = set()
        for row in existing_rows:
            pid = (row.get("post_id") or "").strip()
            phash = (row.get("content_hash") or "").strip()
            if pid:
                existing_ids.add(pid)
            if phash:
                existing_hashes.add(phash)

        # Thêm bài mới không trùng
        added = 0
        for rec in new_records:
            pid = str(rec.post_id or "").strip()
            phash = str(rec.content_hash or "").strip()
            if (pid and pid in existing_ids) or (phash and phash in existing_hashes):
                continue
            row = _record_to_row(rec)
            existing_rows.append(row)
            if pid:
                existing_ids.add(pid)
            if phash:
                existing_hashes.add(phash)
            added += 1

        # Sắp xếp cũ → mới
        existing_rows = self._sort_rows(existing_rows)

        # Cập nhật lại index
        for i, row in enumerate(existing_rows, 1):
            row["index"] = str(i)

        self._write(existing_rows)
        log.info("CSV: %s bài mới được thêm vào, tổng %s dòng → %s",
                 added, len(existing_rows), self.output_path)

    def _read_existing(self) -> List[Dict[str, str]]:
        """Đọc CSV hiện tại nếu có."""
        if not self.output_path.exists():
            return []
        try:
            with self.output_path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                rows = [dict(row) for row in reader]
            # Đảm bảo mọi cột đều tồn tại
            normalized = []
            for row in rows:
                norm = {col: row.get(col, "") for col in CSV_COLUMNS}
                normalized.append(norm)
            return normalized
        except Exception as exc:
            log.warning("Không đọc được CSV cũ (%s) — sẽ tạo mới.", exc)
            return []

    @staticmethod
    def _sort_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Sắp xếp cũ → mới; không có ngày → cuối."""
        def key(r):
            t = (r.get("created_time") or "").strip()
            return (0, t) if t else (1, "")
        return sorted(rows, key=key)

    def _write(self, rows: List[Dict[str, str]]) -> None:
        """Ghi CSV với UTF-8 BOM."""
        # Dùng StringIO + encode thủ công để kiểm soát BOM
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=CSV_COLUMNS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

        content = "\ufeff" + buf.getvalue()  # UTF-8 BOM
        self.output_path.write_text(content, encoding="utf-8")
