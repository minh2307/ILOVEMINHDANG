# -*- coding: utf-8 -*-
"""
src/facebook/post_parser.py
============================
Phân tích nội dung bài viết Facebook thô (từ Graph API hoặc browser)
để trích xuất:
  - original_heading   : dòng đầu tiên không rỗng (tối đa 250 ký tự)
  - derived_title      : tiêu đề rút gọn (40–120 ký tự, thuật toán đơn giản)
  - post_type          : TEXT | IMAGE | VIDEO | REEL | LINK | SHARED_POST | UNKNOWN
  - content_preview    : tối đa 300 ký tự
  - has_image / has_video / external_url

Không gọi LLM ở phiên bản này.
"""

import re
from typing import Dict, Any, Optional


# -------- Hằng số --------
MAX_HEADING_LEN = 250
DERIVED_TITLE_MIN = 40
DERIVED_TITLE_MAX = 120
PREVIEW_LEN = 300

# Nhãn thay thế khi không có nội dung chữ
LABEL_IMAGE_ONLY = "[Bài chỉ có hình ảnh]"
LABEL_VIDEO_ONLY = "[Bài chỉ có video]"
LABEL_LINK_ONLY  = "[Bài chia sẻ liên kết]"
LABEL_NO_CONTENT = "[Không tìm thấy nội dung]"

# Câu kết thúc phổ biến để nhận dạng câu hoàn chỉnh
_SENTENCE_END = re.compile(r"([.!?…。！？])\s+")

# URL pattern để phát hiện external_url
_URL_PATTERN = re.compile(r"https?://[^\s\u200b\ufeff\"'<>]+", re.IGNORECASE)


def _strip_extra_whitespace(text: str) -> str:
    """Loại bỏ khoảng trắng, newline thừa nhưng giữ ký tự Unicode."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Nhiều blank line → một blank line
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Khoảng trắng đầu/cuối mỗi dòng
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip()


def extract_original_heading(message: str) -> str:
    """
    Lấy dòng đầu tiên không rỗng, tối đa 250 ký tự.
    Không thay đổi ý nghĩa, không gọi AI.
    """
    if not message or not message.strip():
        return LABEL_NO_CONTENT
    cleaned = _strip_extra_whitespace(message)
    for line in cleaned.split("\n"):
        line = line.strip()
        if line:
            return line[:MAX_HEADING_LEN]
    return LABEL_NO_CONTENT


def derive_title(message: str, original_heading: str = "") -> str:
    """
    Tạo tiêu đề ngắn (40–120 ký tự) bằng thuật toán đơn giản:
    1. Dùng câu đầu tiên nếu vừa tầm.
    2. Nếu câu đầu ≤ DERIVED_TITLE_MAX → dùng nguyên.
    3. Nếu câu đầu quá dài → cắt tại dấu câu gần nhất hoặc từ gần nhất.
    4. Không thêm thông tin không có trong bài.
    5. Không dùng từ giật tít.
    """
    source = (message or original_heading or "").strip()
    if not source or source.startswith("["):
        # Bài không có text → derived_title giống original_heading
        return original_heading or LABEL_NO_CONTENT

    cleaned = _strip_extra_whitespace(source)
    # Ghép thành một dòng để phân tích câu
    single_line = " ".join(cleaned.split("\n")).strip()

    # Tách câu đầu tiên
    first_sentence = _extract_first_sentence(single_line)

    if len(first_sentence) <= DERIVED_TITLE_MAX:
        result = first_sentence
    else:
        # Rút gọn bằng cách cắt tại vị trí gần cuối từ
        result = _truncate_at_word(first_sentence, DERIVED_TITLE_MAX)

    # Nếu quá ngắn, thêm nội dung từ câu tiếp theo
    if len(result) < DERIVED_TITLE_MIN:
        result = _truncate_at_word(single_line, DERIVED_TITLE_MAX)

    result = result.strip(" .…")
    if not result:
        result = original_heading[:DERIVED_TITLE_MAX] if original_heading else LABEL_NO_CONTENT

    return result[:DERIVED_TITLE_MAX]


def _extract_first_sentence(text: str) -> str:
    """Lấy câu đầu tiên (kết thúc bằng dấu câu hoặc toàn bộ text nếu không có dấu câu)."""
    match = _SENTENCE_END.search(text)
    if match:
        return text[:match.end()].strip()
    # Không tìm thấy dấu câu → dùng toàn bộ (sẽ được cắt ở ngoài)
    return text.strip()


def _truncate_at_word(text: str, max_len: int) -> str:
    """Cắt text tại ranh giới từ gần nhất, thêm '…' nếu bị cắt."""
    if len(text) <= max_len:
        return text
    # Tìm vị trí space gần nhất trước max_len
    cut = text[:max_len]
    last_space = cut.rfind(" ")
    if last_space > max_len // 2:
        cut = cut[:last_space]
    return cut.rstrip(" ,;:") + "…"


def detect_post_type(
    message: Optional[str],
    has_image: bool,
    has_video: bool,
    attachments: Optional[Dict] = None,
    story_type: Optional[str] = None,
) -> str:
    """
    Phân loại bài viết:
    TEXT | IMAGE | VIDEO | REEL | LINK | SHARED_POST | UNKNOWN
    """
    # Reel
    if story_type in ("added_video", "mobile_status_update") and has_video:
        if attachments:
            data = attachments.get("data", [])
            for item in data:
                media_type = item.get("type", "")
                if media_type in ("video_inline", "video_share"):
                    subtype = item.get("subattachments", {})
                    if "reel" in str(item).lower():
                        return "REEL"
        return "VIDEO"

    if has_video:
        return "VIDEO"

    if attachments:
        data = attachments.get("data", [])
        for item in data:
            t = item.get("type", "").lower()
            if t in ("photo", "sticker"):
                has_image = True
            if t == "share":
                return "LINK"
            if t == "event":
                return "SHARED_POST"
            if "video" in t:
                return "VIDEO"

    if has_image:
        return "IMAGE"

    if message:
        if _URL_PATTERN.search(message):
            return "LINK"
        return "TEXT"

    return "UNKNOWN"


def extract_external_url(message: Optional[str], attachments: Optional[Dict] = None) -> str:
    """Trích URL ngoài (không phải facebook.com) từ message hoặc attachments."""
    # Thử attachment trước
    if attachments:
        data = attachments.get("data", [])
        for item in data:
            url = item.get("url") or item.get("unshimmed_url") or ""
            if url and "facebook.com" not in url:
                return url

    # Thử message
    if message:
        for m in _URL_PATTERN.finditer(message):
            url = m.group(0)
            if "facebook.com" not in url and "fb.com" not in url:
                return url.rstrip(".,;)")

    return ""


def build_no_text_heading(has_image: bool, has_video: bool, external_url: str) -> str:
    """Trả về nhãn phù hợp khi bài không có nội dung chữ."""
    if has_video:
        return LABEL_VIDEO_ONLY
    if has_image:
        return LABEL_IMAGE_ONLY
    if external_url:
        return LABEL_LINK_ONLY
    return LABEL_NO_CONTENT


def parse_post(raw: Dict[str, Any], source_page: str = "robolearnai") -> Dict[str, Any]:
    """
    Nhận dict bài viết thô (từ Graph API hoặc browser scraper)
    và trả về dict chuẩn hoá với tất cả các trường yêu cầu.

    Các key mong đợi trong `raw`:
        id, message, created_time, permalink_url,
        attachments, status_type, full_picture,
        has_video (bool), error_message
    """
    post_id = str(raw.get("id") or "")
    message = (raw.get("message") or "").strip()
    created_time = raw.get("created_time") or ""
    post_url = raw.get("permalink_url") or raw.get("post_url") or ""
    attachments = raw.get("attachments")
    story_type = raw.get("status_type") or raw.get("story_type") or ""
    full_picture = raw.get("full_picture") or ""
    crawl_method = raw.get("crawl_method") or "UNKNOWN"
    crawled_at = raw.get("crawled_at") or ""
    crawl_status = raw.get("crawl_status") or "SUCCESS"
    error_message = raw.get("error_message") or ""

    # Phát hiện media
    has_image = bool(full_picture or raw.get("has_image", False))
    has_video = bool(raw.get("has_video", False))
    if attachments:
        for item in attachments.get("data", []):
            t = item.get("type", "")
            if "photo" in t or "sticker" in t:
                has_image = True
            if "video" in t:
                has_video = True

    external_url = extract_external_url(message, attachments)
    post_type = detect_post_type(message, has_image, has_video, attachments, story_type)

    # original_heading
    if message:
        original_heading = extract_original_heading(message)
    else:
        original_heading = build_no_text_heading(has_image, has_video, external_url)

    # derived_title
    derived_title = derive_title(message, original_heading)

    # content_preview
    content_preview = _strip_extra_whitespace(message)[:PREVIEW_LEN] if message else ""

    return {
        "source_page": source_page,
        "post_id": post_id,
        "post_url": post_url,
        "created_time": created_time,
        "original_heading": original_heading,
        "derived_title": derived_title,
        "content_preview": content_preview,
        "post_type": post_type,
        "has_image": has_image,
        "has_video": has_video,
        "external_url": external_url,
        "crawl_method": crawl_method,
        "crawled_at": crawled_at,
        "crawl_status": crawl_status,
        "error_message": error_message,
    }
