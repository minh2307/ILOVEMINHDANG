from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


class ReelUrlError(ValueError):
    pass


class MultipleReelUrlsError(ReelUrlError):
    pass


_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}


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
