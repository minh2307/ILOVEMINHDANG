import time
import threading
import logging
import subprocess
from typing import Callable, Set
from reel_validator import is_facebook_reel
from logger import log_exception

logger = logging.getLogger("fb_downloader")

class ClipboardMonitor(threading.Thread):
    """
    Background worker theo dõi Clipboard của hệ điều hành.
    Không block main thread/UI và tự động bắt đầu tải khi phát hiện Facebook Reel.
    """

    def __init__(self, callback: Callable[[str], None], duplicate_checker = None, interval_seconds: float = 0.5):
        """
        Khởi tạo ClipboardMonitor.
        
        Args:
            callback (Callable[[str], None]): Hàm sẽ được gọi khi phát hiện Reel mới.
            duplicate_checker (DuplicateChecker): Bộ kiểm tra trùng lặp link.
            interval_seconds (float): Khoảng thời gian nghỉ giữa các lần kiểm tra clipboard.
        """
        super().__init__()
        self.callback = callback
        self.duplicate_checker = duplicate_checker
        self.interval = interval_seconds
        self.daemon = True
        self.running = False
        self.last_content = ""
        self.processed_urls: Set[str] = set()

    def get_clipboard_text(self) -> str:
        """
        Lấy nội dung văn bản từ clipboard của hệ điều hành.
        Hỗ trợ Linux qua xclip, xsel và fallback các nền tảng khác nếu chạy đa nền tảng.
        """
        try:
            # 1. Thử dùng xclip (Linux)
            res = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=1,
                check=True
            )
            return res.stdout.strip()
        except Exception:
            try:
                # 2. Thử dùng xsel (Linux)
                res = subprocess.run(
                    ["xsel", "-clipboard", "-o"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=1,
                    check=True
                )
                return res.stdout.strip()
            except Exception:
                try:
                    # 3. Thử dùng pbpaste (macOS)
                    res = subprocess.run(
                        ["pbpaste"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=1,
                        check=True
                    )
                    return res.stdout.strip()
                except Exception:
                    # 4. Thử dùng ctypes (Windows fallback)
                    try:
                        import ctypes
                        # Mở clipboard
                        if ctypes.windll.user32.OpenClipboard(None):
                            try:
                                # Lấy dữ liệu dạng text (CF_UNICODETEXT = 13)
                                handle = ctypes.windll.user32.GetClipboardData(13)
                                if handle:
                                    # Lấy con trỏ đến dữ liệu
                                    ptr = ctypes.windll.kernel32.GlobalLock(handle)
                                    if ptr:
                                        text = ctypes.c_wchar_p(ptr).value
                                        ctypes.windll.kernel32.GlobalUnlock(handle)
                                        return text.strip() if text else ""
                            finally:
                                ctypes.windll.user32.CloseClipboard()
                    except Exception:
                        pass
        return ""

    def run(self) -> None:
        self.running = True
        # Lưu nội dung ban đầu khi khởi chạy để tránh tải ngay lập tức url cũ đã có sẵn trong clipboard
        self.last_content = self.get_clipboard_text()
        
        logger.info("Clipboard monitor started.")
        
        while self.running:
            try:
                current_content = self.get_clipboard_text()
                # Chỉ xử lý khi nội dung thay đổi và không rỗng
                if current_content and current_content != self.last_content:
                    self.last_content = current_content
                    logger.info("Clipboard changed")
                    
                    if is_facebook_reel(current_content):
                        # Kiểm tra trùng lặp
                        is_dup = False
                        if self.duplicate_checker:
                            is_dup = self.duplicate_checker.is_duplicate(current_content)
                        else:
                            is_dup = current_content in self.processed_urls
                            
                        if not is_dup:
                            logger.info("Facebook Reel detected")
                            self.processed_urls.add(current_content)
                            if self.duplicate_checker:
                                self.duplicate_checker.mark_active(current_content)
                            
                            # Kích hoạt callback trong một thread mới để không block vòng lặp clip monitor
                            threading.Thread(
                                target=self.callback, 
                                args=(current_content,), 
                                daemon=True
                            ).start()
                        else:
                            logger.info(f"Duplicate Facebook Reel skipped: {current_content}")
            except Exception as e:
                log_exception(logger, "Error in clipboard monitoring loop", e)
            
            time.sleep(self.interval)

    def stop(self) -> None:
        """Dừng luồng theo dõi clipboard."""
        self.running = False
