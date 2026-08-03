# -*- coding: utf-8 -*-
"""
src/facebook/browser_client.py
================================
Phương án dự phòng: thu thập bài viết công khai từ Facebook Page
bằng Selenium (đã cài sẵn trong venv của dự án).

Tái sử dụng Selenium + ChromeDriverManager từ visit-like-post.py.
KHÔNG gọi any sharing / joining function.
KHÔNG tự động đăng bài viết.

Giới hạn an toàn:
- Chỉ lấy nội dung công khai mà tài khoản hiện tại được phép xem.
- Dừng an toàn nếu cần đăng nhập mà chưa có session/cookies hợp lệ.
- Không bypass CAPTCHA, checkpoint, 2FA.

THAY ĐỔI (v1.1):
- Nạp cookies từ data/cookies.txt / data/cookies.json (dùng chung visit-like-post.py)
- Dùng Chrome profile cố định từ FB_POSTER_PROFILE nếu có
- Sửa tất cả log %d → %s (tránh TypeError từ SensitiveFilter)
"""

import os
import json
import time
import re
import random
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.common import config as cfg
from src.common.logging_setup import get_logger

log = get_logger("fb_crawler.browser")

# -------- Lazy import Selenium để không crash khi thiếu --------
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    from webdriver_manager.chrome import ChromeDriverManager
    _SELENIUM_OK = True
except ImportError:
    _SELENIUM_OK = False

_POST_URL_RE = re.compile(
    r"https://www\.facebook\.com/[^/]+/(posts|videos|reel|permalink)/[^\s\"']+",
    re.IGNORECASE,
)

# -------- Đường dẫn cookies (dùng chung với visit-like-post.py) --------
_COOKIES_JSON = "data/cookies.json"
_COOKIES_TXT  = "data/cookies.txt"


class BrowserCrawlError(Exception):
    """Lỗi khi crawl bằng browser."""


class BrowserClient:
    """
    Thu thập bài viết từ Facebook Page bằng Selenium.
    Chỉ thu thập nội dung công khai.
    Tự động nạp cookies từ data/cookies.txt hoặc data/cookies.json
    (dùng chung với visit-like-post.py).
    """

    def __init__(
        self,
        page_url: str = "",
        max_posts: int = 100,
        scroll_limit: int = 50,
        headless: bool = True,
    ):
        if not _SELENIUM_OK:
            raise BrowserCrawlError(
                "Selenium chưa được cài đặt. Chạy: pip install selenium webdriver-manager"
            )
        self.page_url = page_url or cfg.FACEBOOK_SOURCE_PAGE_URL
        self.max_posts = max_posts or cfg.FACEBOOK_MAX_POSTS
        self.scroll_limit = scroll_limit or cfg.FACEBOOK_SCROLL_LIMIT
        self.headless = headless if headless is not None else cfg.FACEBOOK_HEADLESS
        self.driver: Optional[Any] = None
        self.wait: Optional[Any] = None

    # ------------------------------------------------------------------ #
    # Khởi tạo / đóng driver                                              #
    # ------------------------------------------------------------------ #
    def _setup_driver(self, use_temp_profile: bool = True) -> None:
        """
        Khởi tạo Selenium Chrome driver.
        Luôn dùng profile tạm (không dùng persistent profile) để tránh
        lock conflict với visit-like-post.py đang chạy song song.
        """
        options = Options()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-notifications")
        options.add_argument("--lang=vi-VN")
        options.add_argument("--remote-debugging-port=0")  # port ngẫu nhiên, tránh conflict
        # Giảm thiểu crash khi không có /dev/shm đủ lớn
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-translate")
        options.add_argument("--metrics-recording-only")
        options.add_argument("--safebrowsing-disable-auto-update")
        options.add_argument("--mute-audio")
        options.add_experimental_option(
            "prefs",
            {
                "intl.accept_languages": "vi-VN,vi,en-US,en",
                "profile.default_content_setting_values.notifications": 2,
            },
        )
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        )
        if self.headless:
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1368,832")

        # KHÔNG dùng persistent profile — dùng profile tạm để tránh lock
        # (visit-like-post.py đang giữ khóa profile chính)
        log.info("ℹ️  Dùng profile tạm (không lock) + sẽ nạp cookies sau")

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.wait = WebDriverWait(self.driver, 15)
        log.info("✅ Browser driver sẵn sàng (headless=%s)", self.headless)

    def _close_driver(self) -> None:
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None

    # ------------------------------------------------------------------ #
    # Nạp cookies (tái sử dụng logic từ visit-like-post.py)               #
    # ------------------------------------------------------------------ #
    def _load_cookies(self) -> bool:
        """
        Nạp cookies từ data/cookies.json hoặc data/cookies.txt.
        Cùng logic với visit-like-post.py._load_cookies_from_file().
        Không ghi đè cookie hay session hiện có một cách có hại.
        Trả về True nếu nạp được ít nhất 1 cookie.
        """
        # 1) Thử JSON trước
        if Path(_COOKIES_JSON).exists():
            log.info("🍪 Tìm thấy cookies JSON: %s", _COOKIES_JSON)
            try:
                with open(_COOKIES_JSON, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                self.driver.delete_all_cookies()
                count = 0
                for c in cookies:
                    cookie_dict = {
                        "name": c.get("name"),
                        "value": c.get("value"),
                        "domain": c.get("domain", ".facebook.com"),
                        "path": c.get("path", "/"),
                    }
                    expiry = c.get("expiry") or c.get("expires")
                    if expiry:
                        cookie_dict["expiry"] = int(expiry)
                    try:
                        self.driver.add_cookie(cookie_dict)
                        count += 1
                    except Exception:
                        try:
                            cookie_dict.pop("domain", None)
                            self.driver.add_cookie(cookie_dict)
                            count += 1
                        except Exception:
                            pass
                if count > 0:
                    log.info("✅ Nạp %s cookies từ JSON", count)
                    return True
            except Exception as exc:
                log.warning("Không đọc được cookies JSON: %s", exc)

        # 2) Thử raw text (c_user=xxx; xs=xxx; ...)
        if Path(_COOKIES_TXT).exists():
            log.info("🍪 Tìm thấy cookies raw text: %s", _COOKIES_TXT)
            try:
                raw = Path(_COOKIES_TXT).read_text(encoding="utf-8").strip()
                if not raw:
                    return False
                self.driver.delete_all_cookies()
                count = 0
                for part in raw.split(";"):
                    part = part.strip()
                    if not part or "=" not in part:
                        continue
                    name, value = part.split("=", 1)
                    try:
                        self.driver.add_cookie({
                            "name": name.strip(),
                            "value": value.strip(),
                            "domain": ".facebook.com",
                            "path": "/",
                        })
                        count += 1
                    except Exception:
                        pass
                if count > 0:
                    log.info("✅ Nạp %s cookies từ raw text", count)
                    return True
            except Exception as exc:
                log.warning("Không đọc được cookies raw text: %s", exc)

        return False

    # ------------------------------------------------------------------ #
    # Kiểm tra đăng nhập                                                  #
    # ------------------------------------------------------------------ #
    def _check_login_required(self) -> bool:
        """Trả True nếu Facebook yêu cầu đăng nhập."""
        try:
            if self.driver.find_elements(By.NAME, "email") or \
               "login" in self.driver.current_url.lower():
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------ #
    # Cuộn và thu thập bài viết                                           #
    # ------------------------------------------------------------------ #
    def _extract_posts_from_dom(self, crawled_at: str) -> List[Dict[str, Any]]:
        """
        Đọc các article từ DOM hiện tại và trích xuất thông tin cơ bản.
        Trả về list[dict] thô — sẽ được parse bởi post_parser.py.
        """
        posts: List[Dict[str, Any]] = []
        seen_urls: set = set()

        articles = self.driver.find_elements(By.XPATH, "//div[@role='article']")
        for art in articles:
            try:
                raw = self._parse_article(art, crawled_at)
                if not raw:
                    continue
                url = raw.get("permalink_url") or raw.get("post_url") or ""
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                posts.append(raw)
            except Exception as exc:
                log.debug("PARSING_ERROR article: %s", exc)
        return posts

    def _parse_article(self, article_el: Any, crawled_at: str) -> Optional[Dict[str, Any]]:
        """Trích xuất thông tin từ một article element."""
        post_url = self._find_post_url(article_el)
        message = self._find_message(article_el)
        created_time = self._find_created_time(article_el)

        has_image = bool(article_el.find_elements(
            By.XPATH, ".//img[not(contains(@src,'emoji')) and not(contains(@class,'Avatar'))]"
        ))
        has_video = bool(article_el.find_elements(
            By.XPATH, ".//video | .//*[@data-pagelet='VideoPlayer']"
        ))

        # Chấp nhận bài có message dù không có URL, bỏ qua nếu cả hai đều rỗng
        if not post_url and not message:
            return None
        # Loại phần tử quá ngắn (tìm kiếm, gợi ý)
        if not post_url and message and len(message) < 15:
            return None

        # Tạo fallback ID nếu không có URL
        post_id = self._url_to_id(post_url)
        if not post_id and message:
            import hashlib
            post_id = "temp_" + hashlib.md5(message.encode("utf-8")).hexdigest()[:10]

        return {
            "id": post_id,
            "message": message,
            "created_time": created_time,
            "permalink_url": post_url,
            "attachments": None,
            "status_type": "",
            "full_picture": "",
            "has_video": has_video,
            "has_image": has_image,
            "crawl_method": "BROWSER",
            "crawled_at": crawled_at,
            "crawl_status": "SUCCESS",
            "error_message": "",
        }

    def _find_post_url(self, art: Any) -> str:
        """Tìm permalink của bài viết trong article element."""
        xpaths = [
            ".//a[contains(@href,'/posts/')]",
            ".//a[contains(@href,'/videos/')]",
            ".//a[contains(@href,'/reel/')]",
            ".//a[contains(@href,'/permalink/')]",
            ".//a[contains(@href,'/photo/')]",
            ".//a[contains(@href,'pfbid')]",           # dạng URL mới
            ".//a[contains(@href,'?story_fbid')]",     # dạng URL cũ
        ]
        for xp in xpaths:
            els = art.find_elements(By.XPATH, xp)
            for el in els:
                href = el.get_attribute("href") or ""
                if href and "facebook.com" in href and "login" not in href:
                    return href.split("?")[0]
        return ""

    def _find_message(self, art: Any) -> str:
        """Lấy nội dung text từ article."""
        xpaths = [
            ".//*[@data-ad-preview='message']",
            ".//div[@dir='auto' and not(ancestor::*[@role='button'])]",
            ".//div[contains(@class, 'xdj266r') and contains(@class, 'x11i5rnm')]", # Class text body mới của Facebook
        ]
        for xp in xpaths:
            els = art.find_elements(By.XPATH, xp)
            if els:
                texts = [e.text.strip() for e in els[:5] if e.text.strip()]
                if texts:
                    return "\n".join(texts)
        try:
            raw = art.text or ""
            lines = [ln for ln in raw.split("\n") if ln.strip()]
            clean = [
                ln for ln in lines
                if ln.strip().lower() not in (
                    "thích", "like", "bình luận", "comment", "chia sẻ", "share", "xem thêm",
                )
            ]
            return "\n".join(clean[:5]).strip()
        except Exception:
            return ""

    def _find_created_time(self, art: Any) -> str:
        """Lấy thời gian đăng từ thẻ <a> có title là ngày."""
        xpaths = [
            ".//abbr[@data-utime]",
            ".//a[contains(@href,'/posts/')]//abbr",
            ".//span[@data-utime]",
            ".//a[contains(@aria-label,'ago') or contains(@aria-label,'at')]",
        ]
        for xp in xpaths:
            els = art.find_elements(By.XPATH, xp)
            for el in els:
                ts = el.get_attribute("data-utime") or ""
                if ts:
                    try:
                        dt = datetime.utcfromtimestamp(int(ts))
                        return dt.isoformat() + "Z"
                    except Exception:
                        pass
                title = el.get_attribute("title") or el.get_attribute("aria-label") or ""
                if title and len(title) > 3:
                    return title
        return ""

    @staticmethod
    def _url_to_id(url: str) -> str:
        """Trích post ID từ URL nếu có thể."""
        if not url:
            return ""
        parts = url.rstrip("/").split("/")
        for part in reversed(parts):
            if part.isdigit():
                return part
        return ""

    # ------------------------------------------------------------------ #
    # Public: fetch_posts                                                   #
    # ------------------------------------------------------------------ #
    def fetch_posts(self) -> List[Dict[str, Any]]:
        """
        Mở Facebook Page, nạp cookies (nếu có), cuộn và thu thập bài viết.
        Trả về list[dict] thô.
        Dừng an toàn nếu yêu cầu đăng nhập sau khi đã thử cookies.
        """
        self._setup_driver()
        crawled_at = datetime.utcnow().isoformat() + "Z"
        posts: List[Dict[str, Any]] = []

        try:
            # Bước 1: Mở facebook.com để set domain trước khi add cookies
            log.info("🌐 Mở facebook.com để chuẩn bị session...")
            self.driver.get("https://www.facebook.com")
            time.sleep(random.uniform(2.0, 3.0))

            # Bước 2: Nạp cookies từ file (dùng chung với visit-like-post.py)
            # (Bỏ qua nếu Chrome profile đã có session sẵn)
            cookie_loaded = self._load_cookies()
            if cookie_loaded:
                log.info("🔄 Reload facebook.com sau khi nạp cookies...")
                self.driver.get("https://www.facebook.com")
                time.sleep(random.uniform(2.5, 4.0))

            # Bước 3: Kiểm tra đã đăng nhập chưa
            if self._check_login_required():
                if cookie_loaded:
                    log.error(
                        "LOGIN_REQUIRED: Cookies không còn hợp lệ. "
                        "Vui lòng cập nhật data/cookies.txt hoặc "
                        "cấu hình FACEBOOK_ACCESS_TOKEN để dùng Graph API."
                    )
                else:
                    log.error(
                        "LOGIN_REQUIRED: Không có session/cookies hợp lệ.\n"
                        "Giải pháp:\n"
                        "  1) Cấu hình FACEBOOK_ACCESS_TOKEN trong .env (khuyến nghị)\n"
                        "  2) Chạy visit-like-post.py một lần để tạo Chrome profile\n"
                        "  3) Copy cookies từ browser vào data/cookies.txt"
                    )
                raise BrowserCrawlError("LOGIN_REQUIRED")

            log.info("✅ Đã xác nhận đăng nhập Facebook")

            # Bước 4: Mở Page cần crawl
            log.info("🌐 Mở Page: %s", self.page_url)
            self.driver.get(self.page_url)
            time.sleep(random.uniform(3.0, 5.0))

            # Chờ nội dung xuất hiện
            try:
                self.wait.until(
                    EC.presence_of_element_located((By.XPATH, "//div[@role='article']"))
                )
            except TimeoutException:
                log.warning("Không tìm thấy article sau 15s — có thể page trống hoặc bị chặn.")

            seen_urls: set = set()
            scroll_count = 0

            while scroll_count <= self.scroll_limit and len(posts) < self.max_posts:
                try:
                    new_items = self._extract_posts_from_dom(crawled_at)
                    for item in new_items:
                        url = item.get("permalink_url") or ""
                        uid = item.get("id") or url
                        if uid and uid not in seen_urls:
                            seen_urls.add(uid)
                            posts.append(item)
                        if len(posts) >= self.max_posts:
                            break

                    log.info(
                        "Cuộn %s/%s — %s bài đã thu thập…",
                        scroll_count, self.scroll_limit, len(posts)
                    )

                    if len(posts) >= self.max_posts:
                        break

                    self.driver.execute_script("window.scrollBy({top: 1500, behavior: 'smooth'});")
                    time.sleep(cfg.FACEBOOK_SCROLL_DELAY_SECONDS + random.uniform(0, 1))
                    scroll_count += 1

                except Exception as scroll_exc:
                    # Browser có thể đóng sau nhiều lần cuộn (OOM, timeout)
                    # Trả về những bài đã thu thập được thay vì crash
                    log.warning("⚠️  Dừng cuộn sớm: %s (giữ lại %s bài)", type(scroll_exc).__name__, len(posts))
                    break

        finally:
            self._close_driver()

        log.info("✅ Browser crawl hoàn tất: %s bài viết", len(posts))
        return posts
