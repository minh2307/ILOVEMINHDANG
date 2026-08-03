# -*- coding: utf-8 -*-
"""
src/exporters/json_exporter.py
================================
Xuất danh sách PostRecord ra file JSON đầy đủ.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Dict, Any

from src.common.logging_setup import get_logger

log = get_logger("fb_crawler.json_exporter")


class JSONExporter:
    """Xuất / cập nhật file JSON."""

    def __init__(self, output_path: str):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, new_records: list) -> None:
        """
        Kết hợp bài cũ (từ file JSON hiện tại) với bài mới.
        Dedup theo post_id hoặc content_hash.
        """
        existing = self._read_existing()

        existing_ids: set = {str(r.get("post_id") or "") for r in existing if r.get("post_id")}
        existing_hashes: set = {str(r.get("content_hash") or "") for r in existing if r.get("content_hash")}

        added = 0
        for rec in new_records:
            pid = str(getattr(rec, "post_id", "") or "").strip()
            phash = str(getattr(rec, "content_hash", "") or "").strip()
            if (pid and pid in existing_ids) or (phash and phash in existing_hashes):
                continue
            try:
                row = asdict(rec)
            except Exception:
                row = rec if isinstance(rec, dict) else vars(rec)
            existing.append(row)
            if pid:
                existing_ids.add(pid)
            if phash:
                existing_hashes.add(phash)
            added += 1

        # Sắp xếp cũ → mới
        existing.sort(key=lambda r: (0, r.get("created_time") or "") if r.get("created_time") else (1, ""))

        # Cập nhật index
        for i, r in enumerate(existing, 1):
            r["index"] = i

        self._write(existing)
        log.info("JSON: %s bài mới được thêm vào, tổng %s → %s",
                 added, len(existing), self.output_path)

    def export_raw(self, data: Dict[str, Any]) -> None:
        """Ghi trực tiếp một dict (dùng cho summary)."""
        self.output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _read_existing(self) -> List[Dict[str, Any]]:
        if not self.output_path.exists():
            return []
        try:
            return json.loads(self.output_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Không đọc được JSON cũ (%s) — sẽ tạo mới.", exc)
            return []

    def _write(self, rows: List[Dict[str, Any]]) -> None:
        self.output_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
