import time
import logging
import shutil
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from config import CHROME_CDP_URL, CHROME_PROFILE_DIR, COOKIES_FILE
from logger import log_exception

logger = logging.getLogger("fb_downloader")

def parse_netscape_cookies(cookies_path: Path) -> list:
    """Đọc tệp Netscape cookies.txt và chuyển thành danh sách dạng dict cho Playwright."""
    cookies = []
    if not cookies_path.exists():
        return cookies
    try:
        with open(cookies_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    domain = parts[0]
                    # Playwright yêu cầu định dạng domain chuẩn
                    path = parts[2]
                    secure = parts[3].upper() == "TRUE"
                    try:
                        expires = float(parts[4])
                    except:
                        expires = time.time() + 86400 * 30
                    name = parts[5]
                    value = parts[6]
                    cookies.append({
                        "name": name,
                        "value": value,
                        "domain": domain,
                        "path": path,
                        "secure": secure,
                        "expires": expires
                    })
    except Exception as e:
        logger.error(f"Error parsing Netscape cookies for Playwright: {e}")
    return cookies

class BrowserManager:
    """
    Quản lý vòng đời kết nối trình duyệt Chrome qua CDP hoặc khởi chạy Chrome trực tiếp (Singleton).
    Chỉ khởi tạo trình duyệt 1 lần, chỉ tạo/đóng các trang (pages) cho mỗi Reel.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(BrowserManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, cdp_url: str = CHROME_CDP_URL):
        if self._initialized:
            return
        self.cdp_url = cdp_url
        self.playwright = None
        self.browser: Browser = None
        self.context: BrowserContext = None
        self._lock = threading.Lock()
        self._initialized = True

    def _inject_cookies_if_any(self):
        """Đồng bộ cookie từ file cookies.txt vào Playwright Context."""
        if not self.context:
            return
        try:
            cookies_path = Path(COOKIES_FILE)
            # Tự động convert cookies thô trước khi nạp nếu có
            from download_manager import check_and_convert_cookies
            check_and_convert_cookies(cookies_path)
            
            parsed_cookies = parse_netscape_cookies(cookies_path)
            if parsed_cookies:
                self.context.add_cookies(parsed_cookies)
                logger.info(f"Loaded {len(parsed_cookies)} cookies into Browser Context successfully.")
        except Exception as e:
            logger.warning(f"Failed to load cookies into Browser Context: {e}")

    def start(self) -> bool:
        """
        Kết nối tới trình duyệt Chrome qua CDP hoặc tự động mở instance Chrome mới nếu kết nối thất bại.
        Chỉ thực hiện một lần duy nhất. Nếu mất kết nối, tự động reconnect.
        """
        with self._lock:
            if self.context is not None:
                try:
                    # Kiểm tra nhanh xem context còn hoạt động tốt
                    test_page = self.context.new_page()
                    test_page.close()
                    return True
                except Exception:
                    logger.warning("Browser context is unresponsive. Trying to reconnect...")
                    self._cleanup_resources()
            
            logger.info("Starting Browser Manager")
            
            # Thử cách 1: Kết nối CDP
            try:
                self.playwright = sync_playwright().start()
                logger.info(f"Connecting to Chrome via CDP: {self.cdp_url}")
                self.browser = self.playwright.chromium.connect_over_cdp(self.cdp_url)
                if self.browser.contexts:
                    self.context = self.browser.contexts[0]
                else:
                    self.context = self.browser.new_context()
                logger.info("Connected to Chrome via CDP successfully.")
                self._inject_cookies_if_any()
                return True
            except Exception as cdp_err:
                logger.warning(f"CDP connection refused/failed ({cdp_err}). Falling back to launching local Chrome...")
                
                # Thử cách 2: Tự khởi chạy Chrome cục bộ với profile riêng
                try:
                    chrome_path = shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("google-chrome-stable")
                    profile_path = str(CHROME_PROFILE_DIR)
                    logger.info(f"Launching local Chrome with profile: {profile_path}")
                    
                    self.context = self.playwright.chromium.launch_persistent_context(
                        user_data_dir=profile_path,
                        executable_path=chrome_path,
                        headless=False,  # Để hiển thị giao diện cho người dùng đăng nhập Facebook nếu cần
                        args=[
                            "--remote-debugging-port=9222",
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                        ]
                    )
                    self.browser = self.context.browser
                    logger.info("Launched local Chrome instance with persistent context successfully.")
                    self._inject_cookies_if_any()
                    return True
                except Exception as launch_err:
                    log_exception(logger, "Failed to launch local Chrome instance too", launch_err)
                    self._cleanup_resources()
                    return False

    def create_page(self) -> Page:
        """Tạo trang (page) mới từ context trình duyệt."""
        if not self.context:
            if not self.start():
                raise Exception("Cannot create Page: Browser Manager failed to connect/launch Chrome.")
        
        try:
            logger.info("Creating Page")
            return self.context.new_page()
        except Exception as e:
            logger.warning("Error creating page, trying to reconnect to Chrome...")
            if self.start():
                try:
                    logger.info("Creating Page")
                    return self.context.new_page()
                except Exception as retry_err:
                    raise Exception(f"Failed to create page after reconnecting: {retry_err}")
            raise Exception(f"Cannot create Page due to browser connection error: {e}")

    def _cleanup_resources(self):
        """Dọn dẹp tài nguyên khi mất kết nối hoặc dừng chương trình."""
        if self.context:
            try:
                self.context.close()
            except:
                pass
            self.context = None
        if self.browser:
            try:
                self.browser.close()
            except:
                pass
            self.browser = None
        if self.playwright:
            try:
                self.playwright.stop()
            except:
                pass
            self.playwright = None

    def stop(self):
        """Đóng kết nối trình duyệt khi kết thúc chương trình."""
        with self._lock:
            logger.info("Closing Browser connection")
            self._cleanup_resources()
            logger.info("Browser Manager stopped")
