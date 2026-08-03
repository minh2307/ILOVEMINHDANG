import json
import logging
from pathlib import Path
from typing import Set
from logger import log_exception
from url_normalizer import normalize_reel_url, normalized_source_identities

logger = logging.getLogger("fb_downloader")

class DuplicateChecker:
    """
    Quản lý việc kiểm tra trùng lặp URL Facebook Reels.
    Tránh tải lại các video đang tải hoặc đã tải thành công và file vẫn còn tồn tại.
    """

    def __init__(self, log_file: str = "download_log.json"):
        self.log_file = Path(log_file)
        self.active_urls: Set[str] = set()

    def mark_active(self, url: str) -> None:
        """Đánh dấu URL đang/đã xử lý trong phiên hiện tại."""
        self.active_urls.add(normalize_reel_url(url))

    def unmark_active(self, url: str) -> None:
        """Xóa đánh dấu URL (ví dụ khi tải bị lỗi để cho phép tải lại)."""
        normalized = normalize_reel_url(url)
        self.active_urls.discard(normalized)

    def _write_empty_log(self) -> None:
        """Khởi tạo file log rỗng khi file bị hỏng."""
        try:
            with open(self.log_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"Cannot recreate empty download log file: {e}")

    def is_duplicate(self, url: str) -> bool:
        """
        Kiểm tra xem URL đã trùng lặp hay chưa.
        
        Args:
            url (str): URL cần kiểm tra.
            
        Returns:
            bool: True nếu trùng (đang tải hoặc đã tải thành công và file vẫn tồn tại), ngược lại False.
        """
        url = normalize_reel_url(url)
        
        # 1. Kiểm tra trùng trong phiên chạy hiện tại
        if url in self.active_urls:
            return True

        # 2. Kiểm tra trùng trong lịch sử download_log.json
        if self.log_file.exists():
            logs = []
            try:
                # Nếu file trống, tự phục hồi lại mảng rỗng
                if self.log_file.stat().st_size == 0:
                    self._write_empty_log()
                else:
                    with open(self.log_file, "r", encoding="utf-8") as f:
                        logs = json.load(f)
            except (json.JSONDecodeError, IOError):
                logger.warning("Download log JSON is corrupted. Re-creating empty log...")
                self._write_empty_log()
            except Exception as e:
                log_exception(logger, "Unexpected error reading download log for duplicate check", e)
            
            for entry in reversed(logs):
                if entry.get("status") != "done":
                    continue
                if url in normalized_source_identities(str(entry.get("url") or "")):
                    return True

        return False

    def find_history_entries(self, url: str) -> list[dict]:
        """Return successful history rows matching normalized source identity."""
        normalized = normalize_reel_url(url)
        if not self.log_file.is_file():
            return []
        try:
            with self.log_file.open("r", encoding="utf-8") as handle:
                rows = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            log_exception(logger, "Cannot inspect downloader history", exc)
            return []
        if not isinstance(rows, list):
            return []
        return [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("status") == "done"
            and normalized in normalized_source_identities(str(row.get("url") or ""))
        ]
