import time
import logging
from pathlib import Path
from playwright.sync_api import Page
from logger import log_exception
from config import OUTPUT_DIR

logger = logging.getLogger("fb_downloader")

class CommentScraper:
    """Chịu trách nhiệm cuộn trang, click mở rộng và thu thập comments từ Facebook Reels."""

    @staticmethod
    def scrape(
        page: Page,
        timeout_seconds: int = 45,
        caption: str = "",
        include_authors: bool = False,
        output_dir: Path | None = None,
    ) -> list:
        logger.info("Loading Comments")
        
        # 0. Click mở Sidebar bình luận nếu nó đang bị ẩn
        try:
            comment_triggers = [
                'div[aria-label*="bình luận" i]',
                'div[aria-label*="comment" i]',
                'div[aria-label*="Comments" i]',
                'div[role="button"]:has-text("bình luận")',
                'div[role="button"]:has-text("comment")',
                'span:has-text("bình luận")',
                'span:has-text("comment")'
            ]
            for trigger in comment_triggers:
                loc = page.locator(trigger)
                count = loc.count()
                clicked_trigger = False
                for i in range(count):
                    btn = loc.nth(i)
                    if btn.is_visible():
                        btn.click(timeout=2000)
                        logger.info(f"Clicked comments panel toggle button using selector: {trigger}")
                        page.wait_for_timeout(1000)
                        clicked_trigger = True
                        break
                if clicked_trigger:
                    break
        except Exception as e:
            logger.debug(f"Optional comments toggle click failed or not needed: {e}")

        # Chờ phần comments hiển thị hoàn toàn
        page.wait_for_timeout(1500)

        # 1. Thử chuyển đổi menu sắp xếp bình luận thành "Tất cả bình luận" / "All comments" nếu có
        try:
            sort_clicked = page.evaluate(r"""() => {
                const buttons = Array.from(document.querySelectorAll('div[role="button"], span[role="button"], div, span'));
                const sortButton = buttons.find(btn => {
                    if (btn.offsetWidth > 0 && btn.offsetHeight > 0) {
                        const text = btn.innerText.trim();
                        return /(Phù hợp nhất|Bình luận hàng đầu|Most relevant|Top comments|Mới nhất|Newest)/i.test(text);
                    }
                    return false;
                });
                if (sortButton) {
                    sortButton.click();
                    return true;
                }
                return false;
            }""")
            if sort_clicked:
                page.wait_for_timeout(1000)
                # Tìm và click "Tất cả bình luận"
                all_comments_clicked = page.evaluate(r"""() => {
                    const items = Array.from(document.querySelectorAll('div[role="menuitem"], div[role="button"], span, div, a'));
                    const allCommentsItem = items.find(item => {
                        if (item.offsetWidth > 0 && item.offsetHeight > 0) {
                            const text = item.innerText.trim();
                            return /^(Tất cả bình luận|All comments)$/i.test(text);
                        }
                        return false;
                    });
                    if (allCommentsItem) {
                        allCommentsItem.click();
                        return true;
                    }
                    return false;
                }""")
                if all_comments_clicked:
                    logger.info("Switched sorting to 'All Comments'")
                    page.wait_for_timeout(1500)
        except Exception as e:
            logger.debug(f"Could not switch sorting menu: {e}")

        start_time = time.time()
        last_comment_count = 0
        no_change_count = 0
        scan_history = []
        scan_index = 0
        unique_comments = []
        seen = set()

        # JS scripts định nghĩa riêng để rõ ràng và dễ bảo trì
        expand_buttons_js = r"""() => {
            const getCommentsContainer = () => {
                const selectors = [
                    'div[role="tabpanel"]',
                    'div[role="dialog"]',
                    'div[role="complementary"]',
                    'div[aria-label*="bình luận" i]',
                    'div[aria-label*="comment" i]',
                    'div[aria-label*="Comments" i]'
                ];
                for (const selector of selectors) {
                    const elements = Array.from(document.querySelectorAll(selector));
                    const visible = elements.find(el => el.offsetWidth > 0 && el.offsetHeight > 0);
                    if (visible) return visible;
                }
                return null;
            };

            const container = getCommentsContainer() || document;
            const buttons = Array.from(container.querySelectorAll('div[role="button"], span[role="button"], span, div, a'));
            let clicked = 0;
            const expandRegex = /(Xem thêm bình luận|Xem các bình luận trước|Xem thêm phản hồi|Xem các bình luận khác|View more comments|View previous comments|View more replies|Xem thêm|Hiển thị thêm|View more|phản hồi|replies)/i;
            
            buttons.forEach(btn => {
                if (btn.offsetWidth > 0 && btn.offsetHeight > 0) {
                    const text = btn.innerText.trim();
                    if (expandRegex.test(text) && !/(Ẩn|Hide)/i.test(text)) {
                        const style = window.getComputedStyle(btn);
                        const isInteractive = style.cursor === 'pointer' || 
                                              btn.getAttribute('role') === 'button' ||
                                              btn.tagName === 'A' ||
                                              (btn.parentElement && window.getComputedStyle(btn.parentElement).cursor === 'pointer');
                        if (isInteractive) {
                            try {
                                btn.click();
                                clicked++;
                            } catch(e) {}
                        }
                    }
                }
            });
            return clicked;
        }"""

        scroll_comments_js = r"""() => {
            const getCommentsContainer = () => {
                const selectors = [
                    'div[role="tabpanel"]',
                    'div[role="dialog"]',
                    'div[role="complementary"]',
                    'div[aria-label*="bình luận" i]',
                    'div[aria-label*="comment" i]',
                    'div[aria-label*="Comments" i]'
                ];
                for (const selector of selectors) {
                    const elements = Array.from(document.querySelectorAll(selector));
                    const visible = elements.find(el => el.offsetWidth > 0 && el.offsetHeight > 0);
                    if (visible) return visible;
                }
                return null;
            };

            const container = getCommentsContainer();
            if (container) {
                container.scrollTop = container.scrollHeight;
                const divs = Array.from(container.querySelectorAll('div'));
                divs.forEach(div => {
                    const style = window.getComputedStyle(div);
                    if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && div.scrollHeight > div.clientHeight) {
                        div.scrollTop = div.scrollHeight;
                    }
                });
                return "scrolled_container";
            }

            const divs = Array.from(document.querySelectorAll('div'));
            const scrollableDivs = divs.filter(div => {
                const style = window.getComputedStyle(div);
                const hasScrollableOverflow = style.overflowY === 'auto' || style.overflowY === 'scroll';
                const isVisible = div.offsetWidth > 0 && div.offsetHeight > 0;
                return hasScrollableOverflow && isVisible && div.scrollHeight > div.clientHeight;
            });
            
            const rightSideDivs = scrollableDivs.filter(div => {
                const rect = div.getBoundingClientRect();
                return rect.left > (window.innerWidth / 2);
            });
            
            const targets = rightSideDivs.length > 0 ? rightSideDivs : scrollableDivs;
            if (targets.length > 0) {
                targets.forEach(div => {
                    div.scrollTop = div.scrollHeight;
                });
                return "scrolled_divs";
            }
            window.scrollTo(0, document.body.scrollHeight);
            return "scrolled_window";
        }"""

        # Trích xuất bình luận từ DOM
        extract_comments_js = r"""(captionText) => {
            const getCommentsContainer = () => {
                const selectors = [
                    'div[role="tabpanel"]',
                    'div[role="dialog"]',
                    'div[role="complementary"]',
                    'div[aria-label*="bình luận" i]',
                    'div[aria-label*="comment" i]',
                    'div[aria-label*="Comments" i]'
                ];
                for (const selector of selectors) {
                    const elements = Array.from(document.querySelectorAll(selector));
                    const visible = elements.find(el => el.offsetWidth > 0 && el.offsetHeight > 0);
                    if (visible) return visible;
                }
                return null;
            };

            const container = getCommentsContainer() || document;
            const results = [];
            const profileLinks = Array.from(container.querySelectorAll('a[href*="facebook.com/"], a[role="link"]'))
                .filter(a => {
                    const name = a.innerText.trim();
                    return name.length >= 2 && 
                           !/^\d+\s*(giờ|phút|ngày|tuần|tháng|năm|hr|min|day|week|mon|ago|h|m|d|w|y|y\.)/i.test(name) && 
                           !/^https?:\/\//.test(name) &&
                           !/(Thích|Like|Phản hồi|Reply|Chia sẻ|Share)/i.test(name);
                });
                
            profileLinks.forEach(link => {
                let parent = link.parentElement;
                let foundForThisLink = false;
                for (let i = 0; i < 4; i++) {
                    if (!parent) break;
                    
                    const textSpans = Array.from(parent.querySelectorAll('span[dir="auto"], div[dir="auto"]'));
                    for (let span of textSpans) {
                        const text = span.innerText.trim();
                        if (text && 
                            text !== link.innerText.trim() && 
                            !/^(Thích|Like|Phản hồi|Reply|Chia sẻ|Share|\d+k?|Xem thêm|View more.*|Xem\s+\d+\s+phản hồi|Xem\s+phản hồi|phản hồi|replies|View\s+\d+\s+replies|View\s+replies|Xem thêm bình luận|Hiển thị thêm|View more comments|Xem các bình luận trước|View previous comments)$/i.test(text) &&
                            !/^\d+\s*(giờ|phút|ngày|tuần|tháng|năm|hr|min|day|week|mon|ago|h|m|d|w|y|y\.)/i.test(text)) {
                            
                            // Kiểm tra loại trừ caption
                            if (captionText) {
                                const cleanText = text.replace(/(Ẩn bớt|Xem thêm|See more|Show less)$/i, '').trim().toLowerCase();
                                const cleanCaption = captionText.replace(/(Ẩn bớt|Xem thêm|See more|Show less)$/i, '').trim().toLowerCase();
                                if (cleanText === cleanCaption || cleanCaption.includes(cleanText) || cleanText.includes(cleanCaption)) {
                                    continue;
                                }
                            }
                            
                            results.push({
                                author: link.innerText.trim() || null,
                                content: text,
                                published_at: null
                            });
                            foundForThisLink = true;
                            break;
                        }
                    }
                    if (foundForThisLink) break;
                    parent = parent.parentElement;
                }
            });
            return results;
        }"""

        while time.time() - start_time < timeout_seconds:
            scan_index += 1
            try:
                # 1. Bấm tất cả các nút mở rộng
                clicked_count = page.evaluate(expand_buttons_js)
                
                # 2. Cuộn vùng bình luận
                page.evaluate(scroll_comments_js)
                
                # Chờ load dữ liệu mới
                page.wait_for_timeout(1500)
                
                # 3. Trích xuất bình luận hiện có (truyền caption vào để lọc bỏ)
                comments = page.evaluate(extract_comments_js, caption)
                
                # Chống trùng lặp
                for comment in comments:
                    if isinstance(comment, dict):
                        content = str(comment.get("content") or "").strip()
                        author = str(comment.get("author") or "").strip() or None
                    else:
                        content = str(comment or "").strip()
                        author = None
                    identity = (author, content)
                    if content and identity not in seen:
                        seen.add(identity)
                        unique_comments.append(
                            {"author": author, "content": content, "published_at": None}
                        )
                
                current_count = len(unique_comments)
                scan_history.append(f"Scan #{scan_index}: {current_count} comments (clicked: {clicked_count})")
                logger.info(f"Scan #{scan_index}: {current_count} comments found (clicks: {clicked_count})")
                
                # Dừng nếu 3 lần liên tiếp không có bình luận mới và không click được nút nào mới
                if current_count == last_comment_count:
                    if clicked_count == 0:
                        no_change_count += 1
                    if no_change_count >= 3:
                        logger.info("No new comments or expand buttons after 3 attempts. Stopping.")
                        break
                else:
                    last_comment_count = current_count
                    no_change_count = 0
            except Exception as e:
                logger.error(f"Error during comments expanding iteration: {e}")
                break

        # Nếu sau khi hoàn thành chỉ thu được 1 (hoặc 0) comment, kích hoạt chế độ debug lưu screenshot/HTML
        if len(unique_comments) <= 1:
            try:
                logger.warning(f"Only {len(unique_comments)} comment(s) found. Saving debug info...")
                
                # Đảm bảo thư mục downloads tồn tại
                downloads_dir = Path(output_dir or OUTPUT_DIR).resolve()
                downloads_dir.mkdir(parents=True, exist_ok=True)
                
                # Lưu HTML và Screenshot
                debug_time = int(time.time())
                html_path = downloads_dir / f"comments_debug_{debug_time}.html"
                screenshot_path = downloads_dir / f"comments_debug_{debug_time}.png"
                
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(page.content())
                page.screenshot(path=str(screenshot_path))
                
                logger.info(f"Debug HTML saved to: {html_path}")
                logger.info(f"Debug Screenshot saved to: {screenshot_path}")
                
                # Ghi lịch sử quét vào log
                logger.info("Scan history log:")
                for hist in scan_history:
                    logger.info(f"  {hist}")
            except Exception as debug_err:
                logger.error(f"Failed to save debug info: {debug_err}")

        logger.info(f"Loaded {len(unique_comments)} Comments")
        if include_authors:
            return unique_comments
        return [comment["content"] for comment in unique_comments]
