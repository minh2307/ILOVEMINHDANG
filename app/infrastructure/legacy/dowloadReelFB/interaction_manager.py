import os
import json
import time
import random
import logging
import threading
from datetime import date
from pathlib import Path
from typing import List, Dict, Any, Optional

from playwright.sync_api import Page, Locator
import config
from logger import log_exception

logger = logging.getLogger("fb_downloader")

# ==============================================================================
# JAVASCRIPT SNIPPETS FOR DOM AUTOMATION
# ==============================================================================

# Script to verify if the post is already liked based on accessibility attributes
VERIFY_POST_LIKE_JS = r"""
() => {
    const buttons = Array.from(document.querySelectorAll('div[role="button"], span[role="button"], button'));
    for (let btn of buttons) {
        const label = btn.getAttribute('aria-label') || '';
        const text = btn.innerText || '';
        
        // Exact or strong matches for Like / Thích button at post level
        if (/^(Thích|Like|Gỡ thích|Unlike|Remove Like)$/i.test(label) || /^(Thích|Like)$/i.test(text.trim())) {
            const isLiked = (
                label.toLowerCase().includes("gỡ thích") || 
                label.toLowerCase().includes("unlike") || 
                label.toLowerCase().includes("remove like") || 
                label.toLowerCase().includes("đã thích") || 
                btn.getAttribute('aria-pressed') === 'true'
            );
            return { found: true, isLiked: isLiked };
        }
        
        // Partial matches excluding comment or reply buttons
        if ((label.includes("Thích") || label.includes("Like")) && 
            !label.includes("bình luận") && !label.includes("comment") && !label.includes("phản hồi")) {
            const isLiked = (
                label.toLowerCase().includes("gỡ thích") || 
                label.toLowerCase().includes("unlike") || 
                label.toLowerCase().includes("remove like") || 
                label.toLowerCase().includes("đã thích") || 
                btn.getAttribute('aria-pressed') === 'true'
            );
            return { found: true, isLiked: isLiked };
        }
    }
    return { found: false, isLiked: false };
}
"""

# Script to click the post Like button if it is not already liked
CLICK_POST_LIKE_JS = r"""
() => {
    const buttons = Array.from(document.querySelectorAll('div[role="button"], span[role="button"], button'));
    let target = null;
    for (let btn of buttons) {
        const label = btn.getAttribute('aria-label') || '';
        const text = btn.innerText || '';
        
        if (/^(Thích|Like|Gỡ thích|Unlike|Remove Like)$/i.test(label) || /^(Thích|Like)$/i.test(text.trim())) {
            target = btn;
            break;
        }
        
        if ((label.includes("Thích") || label.includes("Like")) && 
            !label.includes("bình luận") && !label.includes("comment") && !label.includes("phản hồi")) {
            target = btn;
            break;
        }
    }
    
    if (!target) return { success: false, reason: "button_not_found" };
    
    // Check if already liked
    const labelLower = (target.getAttribute('aria-label') || '').toLowerCase();
    if (labelLower.includes("gỡ thích") || 
        labelLower.includes("unlike") || 
        labelLower.includes("remove like") || 
        labelLower.includes("đã thích") || 
        target.getAttribute('aria-pressed') === 'true') {
        return { success: true, reason: "already_liked", clicked: false };
    }
    
    target.click();
    return { success: true, reason: "clicked", clicked: true };
}
"""

# Script to find a comment by its text content and click its Like button
LIKE_COMMENT_JS = r"""
(args) => {
    const commentText = args[0];
    const authorBadges = args[1];
    const skipAuthorComment = args[2];
    const elements = Array.from(document.querySelectorAll('span[dir="auto"], div[dir="auto"]'));
    let targetSpan = null;
    for (let el of elements) {
        if (el.innerText.trim() === commentText.trim()) {
            targetSpan = el;
            break;
        }
    }
    if (!targetSpan) return { success: false, reason: "comment_text_not_found" };
    
    // Traversal to find the comment wrapper element containing a Like button
    let parent = targetSpan.parentElement;
    let commentContainer = null;
    for (let i = 0; i < 6; i++) {
        if (!parent) break;
        const buttons = Array.from(parent.querySelectorAll('div[role="button"], span[role="button"], span, div, a'));
        const hasLike = buttons.some(btn => {
            const t = btn.innerText.trim();
            return /^(Thích|Like|Me gusta)$/i.test(t);
        });
        if (hasLike) {
            commentContainer = parent;
            break;
        }
        parent = parent.parentElement;
    }
    
    if (!commentContainer) {
        commentContainer = targetSpan.closest('div[role="none"]') || targetSpan.parentElement.parentElement;
    }
    
    if (!commentContainer) return { success: false, reason: "comment_container_not_found" };
    
    // Author badge detection
    if (skipAuthorComment) {
        const escapedBadges = authorBadges.map(b => b.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&'));
        const badgePattern = new RegExp('(^|\\s|\\(|\\b)(' + escapedBadges.join('|') + ')(\\s|\\)|\\b|$)', 'i');
        let isAuthor = false;
        
        const badgeSpans = Array.from(commentContainer.querySelectorAll('span, div, a'));
        for (let span of badgeSpans) {
            const t = span.innerText.trim();
            if (badgePattern.test(t)) {
                isAuthor = true;
                break;
            }
        }
        
        if (isAuthor) {
            return { success: false, reason: "author_comment", isAuthor: true };
        }
    }
    
    // Find the Like button inside commentContainer
    const buttons = Array.from(commentContainer.querySelectorAll('div[role="button"], span[role="button"], span, div, a'));
    const likeButton = buttons.reverse().find(btn => {
        const t = btn.innerText.trim();
        return /^(Thích|Like|Me gusta)$/i.test(t);
    });
    
    if (!likeButton) return { success: false, reason: "like_button_not_found" };
    
    // Check if liked based on text color (blue vs gray)
    const style = window.getComputedStyle(likeButton);
    const color = style.color;
    const rgbMatch = color.match(/rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/);
    let isAlreadyLiked = false;
    if (rgbMatch) {
        const r = parseInt(rgbMatch[1]);
        const g = parseInt(rgbMatch[2]);
        const b = parseInt(rgbMatch[3]);
        if (b > r + 30 && b > g + 30) {
            isAlreadyLiked = true;
        }
    }
    
    if (isAlreadyLiked) {
        return { success: true, reason: "already_liked", alreadyLiked: true };
    }
    
    likeButton.click();
    return { success: true, reason: "clicked" };
}
"""

# Script to verify if a specific comment is liked
VERIFY_COMMENT_LIKE_JS = r"""
(commentText) => {
    const elements = Array.from(document.querySelectorAll('span[dir="auto"], div[dir="auto"]'));
    let targetSpan = null;
    for (let el of elements) {
        if (el.innerText.trim() === commentText.trim()) {
            targetSpan = el;
            break;
        }
    }
    if (!targetSpan) return { success: false, reason: "comment_text_not_found" };
    
    let parent = targetSpan.parentElement;
    let commentContainer = null;
    for (let i = 0; i < 6; i++) {
        if (!parent) break;
        const buttons = Array.from(parent.querySelectorAll('div[role="button"], span[role="button"], span, div, a'));
        const hasLike = buttons.some(btn => {
            const t = btn.innerText.trim();
            return /^(Thích|Like|Me gusta)$/i.test(t);
        });
        if (hasLike) {
            commentContainer = parent;
            break;
        }
        parent = parent.parentElement;
    }
    
    if (!commentContainer) {
        commentContainer = targetSpan.closest('div[role="none"]') || targetSpan.parentElement.parentElement;
    }
    
    if (!commentContainer) return { success: false, reason: "comment_container_not_found" };
    
    const buttons = Array.from(commentContainer.querySelectorAll('div[role="button"], span[role="button"], span, div, a'));
    const likeButton = buttons.reverse().find(btn => {
        const t = btn.innerText.trim();
        return /^(Thích|Like|Me gusta)$/i.test(t);
    });
    
    if (!likeButton) return { success: false, reason: "like_button_not_found" };
    
    const style = window.getComputedStyle(likeButton);
    const color = style.color;
    const rgbMatch = color.match(/rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/);
    let isLiked = false;
    if (rgbMatch) {
        const r = parseInt(rgbMatch[1]);
        const g = parseInt(rgbMatch[2]);
        const b = parseInt(rgbMatch[3]);
        if (b > r + 30 && b > g + 30) {
            isLiked = true;
        }
    }
    return { success: true, isLiked: isLiked };
}
"""

def close_popups(page: Page) -> None:
    """
    Phát hiện và tự động đóng các popup, hộp thoại cookie hoặc yêu cầu
    đăng nhập của Facebook cản trở quá trình tương tác.
    """
    try:
        close_selectors = [
            'div[aria-label="Đóng"]',
            'div[aria-label="Close"]',
            'div[aria-label="Chấp nhận tất cả"]',
            'div[aria-label="Accept all"]',
            'div[role="dialog"] div[role="button"][aria-label*="Đóng" i]',
            'div[role="dialog"] div[role="button"][aria-label*="Close" i]',
            'div[role="dialog"] div[role="button"]:has-text("Đóng")',
            'div[role="dialog"] div[role="button"]:has-text("Close")',
        ]
        for sel in close_selectors:
            loc = page.locator(sel)
            count = loc.count()
            for i in range(count):
                btn = loc.nth(i)
                if btn.is_visible():
                    logger.info(f"[LIKE] Phát hiện popup/hộp thoại cản trở, đang đóng bằng selector: {sel}")
                    btn.click(timeout=1000)
                    page.wait_for_timeout(500)
    except Exception as e:
        logger.debug(f"[LIKE] Không phát hiện hoặc không đóng được popup: {e}")


class InteractionManager:
    """
    Quản lý toàn bộ hành động tương tác với Facebook (Like bài viết, Like comment).
    Đảm bảo tuân thủ thiết kế SOLID, an toàn đa luồng và giả lập hành vi con người.
    """
    _lock = threading.Lock()

    def __init__(self, log_path: Optional[Path] = None):
        """Khởi tạo InteractionManager và đảm bảo file log hoạt động tồn tại."""
        self.log_path = log_path or Path(config.ACTIVITY_LOG_JSON)
        # Danh sách badge nhận dạng tác giả/admin bằng nhiều ngôn ngữ khác nhau
        self.author_badges = ["Author", "Creator", "Admin", "Tác giả", "Người viết", "Quản trị viên", "Người tạo"]
        self._ensure_log_exists()

    def _ensure_log_exists(self) -> None:
        """Đảm bảo file activity_log.json tồn tại, đúng định dạng và được reset khi qua ngày mới."""
        with self._lock:
            today_str = str(date.today())
            default_data = {"date": today_str, "post_like": 0, "comment_like": 0}
            
            if not self.log_path.exists():
                self._write_log(default_data)
            else:
                try:
                    with open(self.log_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict) or "date" not in data:
                        raise ValueError("Invalid activity log schema")
                    
                    # Reset dữ liệu nếu tệp lưu ngày cũ
                    if data.get("date") != today_str:
                        logger.info(f"[LIKE] Bước sang ngày mới ({today_str}), đặt lại giới hạn hoạt động.")
                        self._write_log(default_data)
                except Exception as e:
                    logger.warning(f"[LIKE] Tệp tin activity_log.json bị lỗi hoặc không đọc được. Đang khôi phục: {e}")
                    self._write_log(default_data)

    def _write_log(self, data: Dict[str, Any]) -> None:
        """Ghi đè nội dung cấu trúc dữ liệu hoạt động vào file log."""
        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"[LIKE] Không thể ghi file activity_log.json: {e}")

    def load_activity(self) -> Dict[str, Any]:
        """Đọc và trả về dữ liệu hoạt động trong ngày hiện tại."""
        self._ensure_log_exists()
        with self._lock:
            try:
                with open(self.log_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("date") == str(date.today()):
                        return data
            except Exception as e:
                logger.error(f"[LIKE] Lỗi khi đọc log hoạt động: {e}")
            return {"date": str(date.today()), "post_like": 0, "comment_like": 0}

    def activity_limit(self, action_type: str) -> bool:
        """
        Kiểm tra xem loại hoạt động tương ứng đã vượt quá giới hạn hàng ngày chưa.
        
        Args:
            action_type: 'post_like' hoặc 'comment_like'
            
        Returns:
            bool: True nếu đã đạt giới hạn, ngược lại False.
        """
        activity = self.load_activity()
        if action_type == "post_like":
            limit = config.MAX_POST_LIKE_PER_DAY
            current = activity.get("post_like", 0)
            return current >= limit
        elif action_type == "comment_like":
            limit = config.MAX_COMMENT_LIKE_PER_DAY
            current = activity.get("comment_like", 0)
            return current >= limit
        return False

    def record_activity(self, action_type: str) -> None:
        """Tăng số lượng hoạt động tương tác tương ứng lên 1 đơn vị và lưu lại."""
        self._ensure_log_exists()
        with self._lock:
            try:
                with open(self.log_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                today_str = str(date.today())
                if data.get("date") == today_str:
                    if action_type in data:
                        data[action_type] += 1
                    else:
                        data[action_type] = 1
                else:
                    data = {"date": today_str, "post_like": 0, "comment_like": 0}
                    data[action_type] = 1
                    
                self._write_log(data)
            except Exception as e:
                logger.error(f"[LIKE] Gặp lỗi khi cập nhật số lần tương tác: {e}")

    def random_delay(self) -> None:
        """Tạo một khoảng nghỉ ngẫu nhiên để giả lập hành vi click của người dùng thật."""
        delay = random.uniform(config.LIKE_DELAY_MIN, config.LIKE_DELAY_MAX)
        logger.info(f"[LIKE] Nghỉ ngẫu nhiên {delay:.2f} giây...")
        time.sleep(delay)

    def verify_like_success(self, page: Page, comment_text: Optional[str] = None) -> bool:
        """
        Kiểm tra trạng thái nút Like để xác minh hành động đã thành công hay chưa.
        
        Args:
            page: Đối tượng Page Playwright đang mở Reel.
            comment_text: Nếu None thì kiểm tra Like Post, ngược lại kiểm tra Like Comment cụ thể.
            
        Returns:
            bool: True nếu trạng thái nút hiện tại là đã Like, ngược lại False.
        """
        try:
            if comment_text is None:
                res = page.evaluate(VERIFY_POST_LIKE_JS)
                return bool(res.get("isLiked")) if isinstance(res, dict) else False
            else:
                res = page.evaluate(VERIFY_COMMENT_LIKE_JS, comment_text)
                return bool(res.get("isLiked")) if isinstance(res, dict) else False
        except Exception as e:
            logger.warning(f"[LIKE] Không thể xác minh kết quả tương tác: {e}")
            return False

    def detect_author_comment(self, page: Page, comment_text: str) -> bool:
        """
        Xác định xem comment tương ứng có phải là của tác giả (Reel Author, Creator, Admin) hay không.
        
        Args:
            page: Đối tượng Page.
            comment_text: Văn bản nội dung comment.
            
        Returns:
            bool: True nếu là comment của tác giả, ngược lại False.
        """
        js_detect = r"""
        (args) => {
            const commentText = args[0];
            const authorBadges = args[1];
            const elements = Array.from(document.querySelectorAll('span[dir="auto"], div[dir="auto"]'));
            let targetSpan = null;
            for (let el of elements) {
                if (el.innerText.trim() === commentText.trim()) {
                    targetSpan = el;
                    break;
                }
            }
            if (!targetSpan) return false;
            
            let parent = targetSpan.parentElement;
            let commentContainer = null;
            for (let i = 0; i < 6; i++) {
                if (!parent) break;
                const buttons = Array.from(parent.querySelectorAll('div[role="button"], span[role="button"], span, div, a'));
                const hasLike = buttons.some(btn => {
                    const t = btn.innerText.trim();
                    return /^(Thích|Like|Me gusta)$/i.test(t);
                });
                if (hasLike) {
                    commentContainer = parent;
                    break;
                }
                parent = parent.parentElement;
            }
            
            if (!commentContainer) {
                commentContainer = targetSpan.closest('div[role="none"]') || targetSpan.parentElement.parentElement;
            }
            
            if (!commentContainer) return false;
            
            const escapedBadges = authorBadges.map(b => b.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&'));
            const badgePattern = new RegExp('(^|\\s|\\(|\\b)(' + escapedBadges.join('|') + ')(\\s|\\)|\\b|$)', 'i');
            
            const badgeSpans = Array.from(commentContainer.querySelectorAll('span, div, a'));
            for (let span of badgeSpans) {
                const t = span.innerText.trim();
                if (badgePattern.test(t)) {
                    return true;
                }
            }
            return false;
        }
        """
        try:
            return bool(page.evaluate(js_detect, [comment_text, self.author_badges]))
        except Exception as e:
            logger.debug(f"[LIKE] Không phát hiện được tác giả cho bình luận '{comment_text[:20]}...': {e}")
            return False

    def should_like_comment(self, comment_text: str, is_author: bool) -> bool:
        """
        Đánh giá các điều kiện lọc nâng cao của comment (độ dài, liên kết, emoji).
        
        Args:
            comment_text: Nội dung bình luận.
            is_author: Bình luận đó có phải của tác giả hay không.
            
        Returns:
            bool: True nếu thỏa mãn tất cả tiêu chí của config.
        """
        # 1. Skip Author Comment
        if is_author and config.SKIP_AUTHOR_COMMENT:
            return False

        # 2. Minimum length check
        if len(comment_text) < config.LIKE_CONDITION_MIN_LENGTH:
            return False

        # 3. Skip Link check
        if config.LIKE_SKIP_LINK:
            if "http://" in comment_text or "https://" in comment_text or "www." in comment_text:
                return False

        # 4. Skip Emoji only check
        if config.LIKE_SKIP_EMOJI_ONLY:
            if self._is_emoji_only(comment_text):
                return False

        return True

    def _is_emoji_only(self, text: str) -> bool:
        """Hàm trợ giúp kiểm tra chuỗi chỉ chứa emoji và khoảng trắng."""
        cleaned = "".join(text.split())
        if not cleaned:
            return False
            
        for char in cleaned:
            cp = ord(char)
            is_emoji = (
                (0x1F600 <= cp <= 0x1F64F) or    # Emoticons
                (0x1F300 <= cp <= 0x1F5FF) or    # Misc Symbols & Pictographs
                (0x1F680 <= cp <= 0x1F6FF) or    # Transport & Map Symbols
                (0x1F1E0 <= cp <= 0x1F1FF) or    # Flags
                (0x1F900 <= cp <= 0x1F9FF) or    # Supplemental Symbols
                (0x1FA70 <= cp <= 0x1FAFF) or    # Symbols/Pictographs Extended-A
                (0x2600 <= cp <= 0x26FF) or      # Misc Symbols
                (0x2700 <= cp <= 0x27BF) or      # Dingbats
                (0xFE00 <= cp <= 0xFE0F) or      # Variation Selectors
                (0x1F000 <= cp <= 0x1F0FF)       # Mahjong/Domino
            )
            if not is_emoji:
                return False
        return True

    def like_post(self, page: Page) -> bool:
        """
        Thực hiện hành động Like Post Reel hiện tại trên màn hình.
        
        Args:
            page: Đối tượng Page.
            
        Returns:
            bool: True nếu Like thành công hoặc đã Like trước đó, ngược lại False.
        """
        if self.activity_limit("post_like"):
            logger.warning("[LIKE] Activity Limit Reached (Post)")
            return False
            
        try:
            # Kiểm tra trạng thái đã Like chưa
            verify = page.evaluate(VERIFY_POST_LIKE_JS)
            if verify.get("found") and verify.get("isLiked"):
                logger.info("[LIKE] Already liked")
                return True
                
            # Click nút Like
            click_res = page.evaluate(CLICK_POST_LIKE_JS)
            if not click_res.get("success"):
                logger.warning(f"[LIKE] Like Failed: {click_res.get('reason')}")
                return False
                
            if click_res.get("clicked"):
                logger.info("[LIKE] Post liked")
                self.random_delay()
                self.record_activity("post_like")
                
                # Xác minh lại sau khi click
                if self.verify_like_success(page):
                    return True
                else:
                    logger.warning("[LIKE] Like Failed (Verification failed)")
                    return False
            else:
                logger.info("[LIKE] Already liked (detected during execution)")
                return True
                
        except Exception as e:
            logger.warning(f"[LIKE] Like Failed do lỗi phát sinh: {e}")
            return False

    def like_comment(self, page: Page, comment_text: str, stats: Optional[Dict[str, Any]] = None) -> bool:
        """
        Thực hiện hành động Like một comment dựa trên nội dung text của nó.
        
        Args:
            page: Đối tượng Page.
            comment_text: Nội dung chuỗi comment cần Like.
            stats: Dictionary để cộng dồn các thông số skip nếu có.
            
        Returns:
            bool: True nếu Like thành công hoặc đã Like trước đó, ngược lại False.
        """
        if self.activity_limit("comment_like"):
            logger.warning("[LIKE] Activity Limit Reached (Comment)")
            if stats is not None:
                stats["activityLimitReached"] = True
            return False

        try:
            # Gửi tín hiệu cào/click qua JS
            res = page.evaluate(
                LIKE_COMMENT_JS, 
                [comment_text, self.author_badges, config.SKIP_AUTHOR_COMMENT]
            )
            
            if not res.get("success"):
                reason = res.get("reason")
                if reason == "author_comment":
                    logger.info(f"[LIKE] Skip Author Comment: '{comment_text[:25]}...'")
                    if stats is not None:
                        stats["skippedAuthorComments"] += 1
                else:
                    logger.warning(f"[LIKE] Like Failed: {reason} for comment '{comment_text[:25]}...'")
                return False
                
            if res.get("alreadyLiked"):
                logger.info(f"[LIKE] Already liked comment: '{comment_text[:25]}...'")
                return True
                
            # Đã thực hiện click Like
            logger.info(f"[LIKE] Comment Liked: '{comment_text[:25]}...'")
            self.random_delay()
            self.record_activity("comment_like")
            
            # Xác minh lại thành công
            if self.verify_like_success(page, comment_text):
                return True
            else:
                logger.warning(f"[LIKE] Like Failed (Comment verification failed): '{comment_text[:25]}...'")
                return False
                
        except Exception as e:
            logger.warning(f"[LIKE] Like Failed do lỗi phát sinh khi xử lý comment: {e}")
            return False

    def like_comments(self, page: Page, comments: List[str]) -> Dict[str, Any]:
        """
        Điểu phối việc Like danh sách comments dựa vào các thiết lập chế độ trong config.
        
        Args:
            page: Đối tượng Page.
            comments: Danh sách đầy đủ comment thu được từ scraper.
            
        Returns:
            Dict: Thống kê số lượng comment đã like, đã skip, và trạng thái giới hạn.
        """
        stats = {
            "likedComments": 0,
            "skippedAuthorComments": 0,
            "skippedSpamComments": 0,
            "activityLimitReached": False
        }
        
        if not config.ENABLE_COMMENT_LIKE or not comments:
            return stats
            
        mode = config.COMMENT_LIKE_MODE.upper()
        logger.info(f"[LIKE] Bắt đầu quá trình tương tác comments (Chế độ: {mode})")
        
        # Phân loại danh sách ứng viên hợp lệ
        candidates = []
        for c in comments:
            is_author = self.detect_author_comment(page, c)
            if is_author:
                if config.SKIP_AUTHOR_COMMENT:
                    logger.info(f"[LIKE] Skip Author Comment: '{c[:25]}...'")
                    stats["skippedAuthorComments"] += 1
                    continue
            
            # Kiểm tra bộ lọc CONDITION
            if mode == "CONDITION":
                if not self.should_like_comment(c, is_author):
                    logger.info(f"[LIKE] Skip Spam Comment: '{c[:25]}...'")
                    stats["skippedSpamComments"] += 1
                    continue
                    
            candidates.append(c)

        # Chọn danh sách comment sẽ Like theo chế độ
        target_comments = []
        if mode == "TOP_N":
            target_comments = candidates[:config.COMMENT_LIKE_TOP_N]
        elif mode == "RANDOM":
            num_to_like = random.randint(config.COMMENT_LIKE_RANDOM_MIN, config.COMMENT_LIKE_RANDOM_MAX)
            num_to_like = min(num_to_like, len(candidates))
            if candidates:
                target_comments = random.sample(candidates, num_to_like)
        elif mode == "CONDITION":
            target_comments = candidates
            
        # Tiến hành click Like lần lượt
        for comment_text in target_comments:
            if self.activity_limit("comment_like"):
                logger.warning("[LIKE] Activity Limit Reached (Comment limit hit during execution)")
                stats["activityLimitReached"] = True
                break
                
            # Random skip rate simulator
            if random.random() < config.RANDOM_SKIP_RATE:
                logger.info(f"[LIKE] Random Skip comment: '{comment_text[:25]}...'")
                continue
                
            close_popups(page)
            success = self.like_comment(page, comment_text, stats)
            if success:
                stats["likedComments"] += 1
                
        return stats
