import json
import logging
from pathlib import Path
from datetime import datetime
import threading
from typing import List, Dict, Any

from config import METADATA_FILE
from logger import log_exception

logger = logging.getLogger("fb_downloader")

class MetadataManager:
    """
    Quản lý thông tin metadata lưu trữ file đã tải xuống.
    Đảm bảo an toàn luồng (thread-safe) và tự động tạo lại nếu file bị hỏng hoặc mất.
    """
    
    def __init__(self, filepath: str = METADATA_FILE):
        self.filepath = Path(filepath)
        self._lock = threading.Lock()
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """Đảm bảo file metadata tồn tại và có cấu trúc JSON hợp lệ."""
        with self._lock:
            if not self.filepath.exists():
                self._write_empty_metadata()
            else:
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        json.load(f)
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning("Metadata file is corrupted. Re-creating...")
                    self._write_empty_metadata()

    def _write_empty_metadata(self) -> None:
        """Tạo file metadata rỗng."""
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=4)
            logger.info("Metadata updated")
        except IOError as e:
            log_exception(logger, "Cannot create metadata file", e)

    def load(self) -> List[Dict[str, Any]]:
        """Đọc danh sách metadata từ file."""
        self._ensure_file_exists()
        with self._lock:
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log_exception(logger, "Error loading metadata", e)
                return []

    def save(self, data: List[Dict[str, Any]]) -> None:
        """Lưu danh sách metadata vào file."""
        with self._lock:
            try:
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                logger.info("Metadata updated")
            except Exception as e:
                log_exception(logger, "Error saving metadata", e)

    def add_record(self, filename: str, download_time: str = None) -> None:
        """Thêm một record mới vào metadata."""
        if not download_time:
            download_time = datetime.now().isoformat()
        
        data = self.load()
        # Tránh trùng lặp filename trong metadata
        data = [item for item in data if item.get("filename") != filename]
        data.append({
            "filename": filename,
            "download_time": download_time
        })
        self.save(data)

    def remove_record(self, filename: str) -> None:
        """Xóa một record khỏi metadata dựa vào filename."""
        data = self.load()
        initial_len = len(data)
        data = [item for item in data if item.get("filename") != filename]
        if len(data) < initial_len:
            self.save(data)
