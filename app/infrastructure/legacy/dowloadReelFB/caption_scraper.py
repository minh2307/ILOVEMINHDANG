import re
import logging
from pathlib import Path
from playwright.sync_api import Page
from logger import log_exception
from config import OUTPUT_DIR

logger = logging.getLogger("fb_downloader")

class CaptionScraper:
    """Chịu trách nhiệm trích xuất Caption của Facebook Reels."""

    @staticmethod
    def scrape(page: Page, output_dir=None) -> str:
        logger.info("Extracting Caption")
        try:
            # 1. Tự động click nút "Xem thêm" / "See more" nếu có để mở rộng nội dung
            try:
                # Tìm các phần tử có văn bản Xem thêm/See more
                see_more_locators = [
                    'span:has-text("Xem thêm")',
                    'span:has-text("See more")',
                    'div[role="button"]:has-text("Xem thêm")',
                    'div[role="button"]:has-text("See more")'
                ]
                for selector in see_more_locators:
                    btns = page.locator(selector)
                    count = btns.count()
                    for i in range(count):
                        btn = btns.nth(i)
                        if btn.is_visible():
                            btn.click(timeout=1500)
                            page.wait_for_timeout(500)
            except Exception as e:
                logger.debug(f"Optional 'See more' expand failed or not found: {e}")

            # 2. Thử nhiều selectors khác nhau để lấy văn bản caption
            selectors = [
                'div[role="main"] span[dir="auto"]',
                'h2 ~ div span[dir="auto"]',
                'div[data-pagelet="Reels"] div[dir="auto"]',
                'div[role="dialog"] span[dir="auto"]',
                'span[dir="auto"]'
            ]
            
            caption_text = ""
            for selector in selectors:
                try:
                    locators = page.locator(selector)
                    count = locators.count()
                    longest_text = ""
                    for i in range(count):
                        text = locators.nth(i).inner_text().strip()
                        # Lọc các text không phải caption (nút bấm, thời gian, tên)
                        if (len(text) > len(longest_text) and 
                            len(text) > 5 and
                            not re.search(r"^(Thích|Like|Phản hồi|Reply|Chia sẻ|Share|Xem thêm|See more)$", text, re.IGNORECASE) and
                            not re.search(r"^\d+\s*(giờ|phút|ngày|tuần|tháng|năm|hr|min|day|week|mon|ago)", text, re.IGNORECASE)):
                            longest_text = text
                    if longest_text:
                        caption_text = longest_text
                        break
                except Exception:
                    continue
            
            if caption_text:
                logger.info("Caption Found")
                return caption_text
            
            # Fallback: Chụp ảnh màn hình và dump HTML làm tài liệu debug nếu không tìm thấy caption
            logger.warning("Caption not found with standard selectors. Taking screenshot and dumping HTML...")
            try:
                diagnostics_dir = Path(output_dir or OUTPUT_DIR).resolve()
                diagnostics_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(diagnostics_dir / "caption_failed_screenshot.png"))
                with open(diagnostics_dir / "caption_failed_dump.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception as se:
                logger.error(f"Failed to save caption debug artifacts: {se}")
                
            return ""
        except Exception as e:
            log_exception(logger, "Error in CaptionScraper", e)
            return ""
