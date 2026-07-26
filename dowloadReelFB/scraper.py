import logging
import time
import random
from browser_manager import BrowserManager
from caption_scraper import CaptionScraper
from comment_scraper import CommentScraper
from logger import log_exception
import config

logger = logging.getLogger("fb_downloader")

class ReelScraper:
    """Điều phối toàn bộ quá trình mở page, cào caption, comments, tương tác và tự đóng page."""

    def __init__(self):
        self.browser_manager = BrowserManager()

    def scrape(self, url: str) -> dict:
        """
        Thực hiện cào thông tin Reel và thực hiện các hành động tương tác (Like post, Like comment).
        
        Args:
            url (str): URL của Facebook Reel.

        Returns:
            dict: Chứa caption, comments và các thông số kết quả tương tác.
        """
        result = {
            "caption": "",
            "comments": [],
            "commentDetails": [],
            "postLiked": False,
            "likedComments": 0,
            "skippedAuthorComments": 0,
            "skippedSpamComments": 0,
            "interactionDuration": 0.0,
            "activityLimitReached": False
        }
        page = None
        start_time = time.time()
        try:
            # 1. Khởi chạy / Kết nối tới Chrome nếu chưa có
            self.browser_manager.start()

            # 2. Tạo một tab (page) mới cho lượt tải này
            page = self.browser_manager.create_page()

            # 3. Mở Reel
            logger.info("Opening Reel")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            
            interaction_mgr = None
            if config.ENABLE_POST_LIKE or config.ENABLE_COMMENT_LIKE:
                from interaction_manager import InteractionManager
                interaction_mgr = InteractionManager()

            # 4. Tương tác Like Post
            if config.ENABLE_POST_LIKE and interaction_mgr is not None:
                try:
                    if interaction_mgr.activity_limit("post_like"):
                        logger.warning("[LIKE] Activity Limit Reached")
                        result["activityLimitReached"] = True
                    elif random.random() < config.RANDOM_SKIP_RATE:
                        logger.info("[LIKE] Random Skip")
                    else:
                        liked = interaction_mgr.like_post(page)
                        result["postLiked"] = liked
                except Exception as e:
                    logger.warning(f"[LIKE] Like Failed (Post): {e}")

            # 5. Trích xuất caption
            result["caption"] = CaptionScraper.scrape(page)
            
            # 6. Thu thập bình luận
            result["commentDetails"] = CommentScraper.scrape(
                page, caption=result["caption"], include_authors=True
            )
            result["comments"] = [
                comment["content"] for comment in result["commentDetails"]
            ]

            # 7. Tương tác Like Comments
            if config.ENABLE_COMMENT_LIKE and interaction_mgr is not None and result["comments"]:
                try:
                    # Nghỉ ngẫu nhiên trước khi tương tác comments
                    interaction_mgr.random_delay()

                    # Tiến hành Like comments
                    comment_stats = interaction_mgr.like_comments(page, result["comments"])
                    result["likedComments"] = comment_stats.get("likedComments", 0)
                    result["skippedAuthorComments"] = comment_stats.get("skippedAuthorComments", 0)
                    result["skippedSpamComments"] = comment_stats.get("skippedSpamComments", 0)
                    if comment_stats.get("activityLimitReached"):
                        result["activityLimitReached"] = True
                except Exception as e:
                    logger.warning(f"[LIKE] Like Failed (Comments): {e}")
            
        except Exception as e:
            log_exception(logger, "Scraper failed to extract or interact data", e)
        finally:
            result["interactionDuration"] = round(time.time() - start_time, 2)
            if page:
                try:
                    logger.info("Closing Page")
                    page.close()
                except Exception as pe:
                    logger.error(f"Error closing page: {pe}")
                    
        return result
