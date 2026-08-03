"""Historical selectors; not used by the verified publisher."""

LOGIN_INPUTS = 'input[name="email"], input[name="pass"]'
REEL_CAPTION = '[data-ad-comet-preview="message"], div[dir="auto"]'
COMMENT_ITEMS = 'div[role="article"] div[dir="auto"]'
PAGE_POSTS = 'div[role="feed"] div[role="article"]'
COMPOSER_ENTRY = '[aria-label*="Create a post"], [aria-label*="Tạo bài viết"]'
COMPOSER_TEXT = 'div[role="textbox"][contenteditable="true"]'
PUBLISH_BUTTON = 'div[role="button"]:has-text("Post"), div[role="button"]:has-text("Đăng")'
COMMENT_INPUT = 'div[role="textbox"][contenteditable="true"]'
JOIN_BUTTON = 'div[role="button"]:has-text("Join group"), div[role="button"]:has-text("Tham gia nhóm")'
