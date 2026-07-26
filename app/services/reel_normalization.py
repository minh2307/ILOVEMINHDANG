from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from app.models.results import DownloadComment


class ReelUrlError(ValueError):
    pass


class MultipleReelUrlsError(ReelUrlError):
    pass


_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}
_SEE_MORE_SUFFIX = re.compile(
    r"(?:\s|\n)*(?:xem thêm|see more|hiển thị thêm|view more|ẩn bớt|show less)(?:\s|\n)*$",
    re.IGNORECASE,
)
_UI_LABELS = {
    "thích", "like", "phản hồi", "reply", "chia sẻ", "share", "xem thêm",
    "see more", "hiển thị thêm", "view more", "tất cả bình luận", "all comments",
    "bình luận", "comments", "viết bình luận", "write a comment",
}


def split_source_urls(value: str) -> list[str]:
    if not isinstance(value, str):
        raise ReelUrlError("Facebook Reel URL must be a string")
    candidates = _URL_PATTERN.findall(value.strip())
    if not candidates and value.strip():
        candidates = [value.strip()]
    return [candidate.rstrip(".,;)") for candidate in candidates if candidate.strip()]


def _normalize_one(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ReelUrlError(f"Invalid Facebook Reel URL: {value!r}")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in _FACEBOOK_HOSTS:
        hostname = "www.facebook.com"
    elif hostname == "fb.watch":
        hostname = "fb.watch"
    else:
        raise ReelUrlError(f"Unsupported Facebook host: {parsed.hostname}")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(("https", hostname, path, "", ""))


def normalized_source_identities(value: str) -> set[str]:
    identities: set[str] = set()
    for candidate in split_source_urls(value):
        try:
            identities.add(_normalize_one(candidate))
        except ReelUrlError:
            continue
    return identities


def normalize_reel_url(value: str) -> str:
    candidates = split_source_urls(value)
    if not candidates:
        raise ReelUrlError("Facebook Reel URL is empty")
    identities = {_normalize_one(candidate) for candidate in candidates}
    if len(identities) > 1:
        raise MultipleReelUrlsError(
            "Input contains multiple different Facebook URLs; provide exactly one Reel source"
        )
    return identities.pop()


def original_source_url(value: str) -> str:
    candidates = split_source_urls(value)
    if not candidates:
        raise ReelUrlError("Facebook Reel URL is empty")
    normalize_reel_url(value)
    return candidates[0].strip()


def _normalize_visible_text(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    compact: list[str] = []
    for line in lines:
        if line or (compact and compact[-1]):
            compact.append(line)
    while compact and not compact[-1]:
        compact.pop()
    return "\n".join(compact).strip()


def normalize_caption(value: object) -> str:
    text = _normalize_visible_text(value)
    previous = None
    while text != previous:
        previous = text
        text = _SEE_MORE_SUFFIX.sub("", text).strip()
    return text


def normalize_comments(values: Iterable[object]) -> list[DownloadComment]:
    normalized: list[DownloadComment] = []
    seen_content: set[str] = set()
    for item in values or []:
        if isinstance(item, Mapping):
            content = _normalize_visible_text(item.get("content"))
            author_value = _normalize_visible_text(item.get("author"))
            author = author_value or None
        else:
            content = _normalize_visible_text(item)
            author = None
        previous = None
        while content != previous:
            previous = content
            content = _SEE_MORE_SUFFIX.sub("", content).strip()
        if not content or content.casefold() in _UI_LABELS or content in seen_content:
            continue
        seen_content.add(content)
        normalized.append(DownloadComment(author=author, content=content))
    return normalized
