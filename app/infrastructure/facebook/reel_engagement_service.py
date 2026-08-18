from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit


ReactionState = Literal["liked", "not_liked", "unknown"]


@dataclass(frozen=True, slots=True)
class FacebookActorIdentity:
    name: str = ""
    actor_id: str | None = None
    profile_url: str | None = None


@dataclass(frozen=True, slots=True)
class FacebookCommentSnapshot:
    identity_key: str
    dom_key: str
    author: FacebookActorIdentity
    text: str
    comment_id: str | None = None
    is_reply: bool = False


class FacebookReelEngagementService:
    """Engage with one Reel through a Playwright page owned by the caller.

    This class deliberately has no browser/context creation code. The
    ``FacebookBrowserWorker`` supplies the page from its shared CDP session and
    serializes access before this service is invoked.
    """

    MAX_SCROLL_ROUNDS = 50
    MAX_NO_NEW_COMMENT_ROUNDS = 3
    _REEL_SCOPE_ATTRIBUTE = "data-codex-active-reel-scope"
    _COMMENT_DOM_ATTRIBUTE = "data-codex-reel-comment-key"
    _REACTION_SELECTOR = (
        'button, [role="button"][aria-label], [role="button"][aria-pressed], '
        'span[role="button"], div[role="button"]'
    )
    _EXPAND_SELECTOR = 'button, [role="button"], a[role="button"]'
    _COMMENT_PANEL_SELECTOR = (
        '[role="button"][aria-label*="comment" i], '
        '[role="button"][aria-label*="bình luận" i], '
        'button[aria-label*="comment" i], '
        'button[aria-label*="bình luận" i]'
    )
    _LIKED_TERMS = (
        "remove like",
        "unlike",
        "remove your like",
        "remove reaction",
        "remove your reaction",
        "gỡ thích",
        "gỡ lượt thích",
        "gỡ cảm xúc",
        "bỏ thích",
        "bỏ lượt thích",
        "xóa lượt thích",
        "xóa cảm xúc",
    )
    _NOT_LIKED_TERMS = ("like", "thích")

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        max_scroll_rounds: int = MAX_SCROLL_ROUNDS,
        max_no_new_comment_rounds: int = MAX_NO_NEW_COMMENT_ROUNDS,
        action_delay_min_seconds: float = 0.25,
        action_delay_max_seconds: float = 0.75,
        navigation_timeout_ms: int = 60_000,
        click_timeout_ms: int = 5_000,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.logger = logger or logging.getLogger(
            "cdha_pipeline.facebook_reel_engagement"
        )
        self.max_scroll_rounds = max(1, int(max_scroll_rounds))
        self.max_no_new_comment_rounds = max(
            1, int(max_no_new_comment_rounds)
        )
        minimum = max(0.0, float(action_delay_min_seconds))
        maximum = max(minimum, float(action_delay_max_seconds))
        self.action_delay_range = (minimum, maximum)
        self.navigation_timeout_ms = max(1, int(navigation_timeout_ms))
        self.click_timeout_ms = max(1, int(click_timeout_ms))
        self._sleep = sleeper

    async def like_reel_and_comments(
        self,
        page: Any,
        reel_url: str,
        like_reel: bool = True,
        like_comments: bool = True,
        like_replies: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": True,
            "reel_url": reel_url,
            "reel_author": "",
            "reel_author_id": None,
            "reel_author_url": None,
            "reel_liked": False,
            "reel_like_clicked": False,
            "comments_found": 0,
            "comments_processed": 0,
            "comments_liked": 0,
            "comments_already_liked": 0,
            "author_comments_skipped": 0,
            "failed": 0,
            "error": None,
        }
        try:
            await self._navigate(page, reel_url)
            await self._ensure_authenticated(page)
            await self._dismiss_blocking_popups(page)
            reel_author = await self._extract_reel_author(page, reel_url)
            result.update(
                reel_url=str(getattr(page, "url", reel_url) or reel_url),
                reel_author=reel_author.name,
                reel_author_id=reel_author.actor_id,
                reel_author_url=reel_author.profile_url,
            )
            self.logger.info("[REEL] Author: %s", reel_author.name or "UNKNOWN")

            if like_reel:
                try:
                    liked, clicked = await self._ensure_reel_liked(page, reel_url)
                    result["reel_liked"] = liked
                    result["reel_like_clicked"] = clicked
                except Exception as exc:
                    self._record_error(
                        result,
                        f"Reel reaction failed: {type(exc).__name__}: {exc}",
                    )
                    self.logger.warning("[REEL] Like failed: %s", exc)

            if like_comments:
                await self._open_comment_panel(page, reel_url)
                comments = await self._load_all_comments(
                    page, reel_url, like_replies=like_replies
                )
                result["comments_found"] = len(comments)
                await self._process_comments(
                    page,
                    reel_url,
                    comments,
                    reel_author,
                    like_replies=like_replies,
                    result=result,
                )
        except Exception as exc:
            self._record_error(result, f"{type(exc).__name__}: {exc}")
            self.logger.warning(
                "[REEL] Engagement failed: %s", result["error"], exc_info=True
            )
        finally:
            self._log_summary(result)
        return result

    async def _navigate(self, page: Any, reel_url: str) -> None:
        normalized_url = reel_url.casefold()
        if not reel_url or not any(
            domain in normalized_url for domain in ("facebook.com", "fb.watch")
        ):
            raise ValueError("A Facebook Reel URL is required")
        await page.goto(
            reel_url,
            wait_until="domcontentloaded",
            timeout=self.navigation_timeout_ms,
        )
        await page.wait_for_timeout(750)
        current = str(getattr(page, "url", "") or "").casefold()
        if any(marker in current for marker in ("/login", "/checkpoint", "/recover")):
            raise RuntimeError("Facebook authentication or checkpoint is required")

    async def _ensure_authenticated(self, page: Any) -> None:
        login_inputs = page.locator('input[name="email"], input[name="pass"]')
        for index in range(min(await login_inputs.count(), 2)):
            if await login_inputs.nth(index).is_visible():
                raise RuntimeError("Facebook account is not logged in")

    async def _dismiss_blocking_popups(self, page: Any) -> None:
        selector = (
            '[role="dialog"] [role="button"][aria-label="Close" i], '
            '[role="dialog"] [role="button"][aria-label="Đóng" i], '
            '[role="dialog"] button[aria-label="Close" i], '
            '[role="dialog"] button[aria-label="Đóng" i]'
        )
        controls = page.locator(selector)
        for index in range(min(await controls.count(), 4)):
            control = controls.nth(index)
            try:
                if await control.is_visible():
                    await control.click(timeout=self.click_timeout_ms)
                    await self._bounded_delay(0.1, 0.25)
            except Exception as exc:
                self.logger.debug("Popup close control disappeared: %s", exc)

    async def _extract_reel_author(self, page: Any, reel_url: str) -> FacebookActorIdentity:
        if not await self._resolve_active_reel_context(page, reel_url):
            return FacebookActorIdentity()
        raw = await page.evaluate(
            r"""({scopeAttribute}) => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                const isComment = (el) => {
                    const article = el.closest('[role="article"]');
                    if (!article) return false;
                    const label = article.getAttribute('aria-label') || '';
                    if (/comment by|bình luận của/i.test(label) ||
                        article.matches('[data-commentid], [data-comment-id]')) {
                        return true;
                    }
                    return [...article.querySelectorAll(
                        'a[href*="comment_id="], a[href*="reply_comment_id="]'
                    )].some((link) => link.closest('[role="article"]') === article);
                };
                const root = [...document.querySelectorAll(`[${scopeAttribute}]`)]
                    .find(visible);
                if (!root) return {name: '', url: null, id: null};
                const links = [...root.querySelectorAll('a[href]')].filter((link) => {
                    if (!visible(link) || isComment(link)) return false;
                    const href = link.getAttribute('href') || '';
                    const name = (link.innerText || link.getAttribute('aria-label') || '').trim();
                    if (name.length < 1 || !/(facebook\.com|^\/)/i.test(href)) return false;
                    if (/\/(reel|watch|videos|photo|groups|events|marketplace)\//i.test(href)) return false;
                    if (/^(like|thích|comment|bình luận|share|chia sẻ|follow|theo dõi)$/i.test(name)) return false;
                    if (/^\d+[smhdw]|^\d+\s*(phút|giờ|ngày|tuần)/i.test(name)) return false;
                    return true;
                });
                const ranked = links.map((link) => {
                    let score = 0;
                    if (link.closest('h1, h2, h3, h4, header')) score += 8;
                    if (link.querySelector('strong') || link.closest('strong')) score += 4;
                    if (link.getAttribute('aria-label')) score += 2;
                    if (/profile\.php|\/people\//i.test(link.href)) score += 2;
                    return {link, score};
                }).sort((a, b) => b.score - a.score);
                const link = ranked.length ? ranked[0].link : null;
                if (!link) return {name: '', url: null, id: null};
                const hovercard = link.getAttribute('data-hovercard') ||
                    link.getAttribute('data-hovercard-prefer-more-content-show') || '';
                const idMatch = hovercard.match(/[?&](?:id|profile_id)=(\d+)/i) ||
                    link.href.match(/\/people\/[^/]+\/(\d+)/i);
                return {
                    name: (link.innerText || link.getAttribute('aria-label') || '').trim(),
                    url: link.href || link.getAttribute('href'),
                    id: idMatch ? idMatch[1] : null,
                };
            }""",
            {"scopeAttribute": self._REEL_SCOPE_ATTRIBUTE},
        )
        raw = raw if isinstance(raw, dict) else {}
        return self._actor_identity(
            name=str(raw.get("name") or ""),
            actor_id=str(raw.get("id") or "") or None,
            profile_url=str(raw.get("url") or "") or None,
        )

    async def _ensure_reel_liked(self, page: Any, reel_url: str) -> tuple[bool, bool]:
        button, state = await self._find_reel_reaction_button(page, reel_url)
        if state == "liked":
            self.logger.info("[REEL] Already liked")
            return True, False
        if button is None or state != "not_liked":
            raise RuntimeError("Reel Like state could not be determined safely")
        await button.click(timeout=self.click_timeout_ms)
        await self._bounded_delay()
        _verified_button, verified_state = await self._find_reel_reaction_button(page, reel_url)
        if verified_state != "liked":
            raise RuntimeError("Reel Like click could not be verified")
        self.logger.info("[REEL] LIKE")
        return True, True

    async def _find_reel_reaction_button(
        self, page: Any, reel_url: str
    ) -> tuple[Any | None, ReactionState]:
        scope = await self._active_reel_scope(page, reel_url)
        if scope is None:
            return None, "unknown"
        controls = scope.locator(self._REACTION_SELECTOR)
        for index in range(min(await controls.count(), 80)):
            control = controls.nth(index)
            try:
                if not await control.is_visible():
                    continue
                inside_comment = await control.evaluate(
                    r"""(el) => {
                        const article = el.closest('[role="article"]');
                        if (!article) return false;
                        const label = article.getAttribute('aria-label') || '';
                        if (/comment by|bình luận của/i.test(label) ||
                            article.matches('[data-commentid], [data-comment-id]')) {
                            return true;
                        }
                        return [...article.querySelectorAll(
                            'a[href*="comment_id="], a[href*="reply_comment_id="]'
                        )].some((link) =>
                            link.closest('[role="article"]') === article
                        );
                    }"""
                )
                if inside_comment:
                    continue
                state = await self._reaction_state(control)
                if state != "unknown":
                    return control, state
            except Exception:
                continue
        return None, "unknown"

    async def _open_comment_panel(self, page: Any, reel_url: str) -> None:
        if await self._discover_comments(page, reel_url, like_replies=False):
            return
        scope = await self._active_reel_scope(page, reel_url)
        if scope is None:
            raise RuntimeError("Active Reel container could not be located")
        controls = scope.locator(self._COMMENT_PANEL_SELECTOR)
        for index in range(min(await controls.count(), 20)):
            control = controls.nth(index)
            try:
                if not await control.is_visible():
                    continue
                label = self._normalize_text(
                    (await control.get_attribute("aria-label")) or ""
                )
                if not any(term in label for term in ("comment", "bình luận")):
                    continue
                await control.click(timeout=self.click_timeout_ms)
                await self._bounded_delay(0.4, 0.8)
                self.logger.info("[REEL] Comment panel opened")
                return
            except Exception as exc:
                self.logger.debug("Comment panel control disappeared: %s", exc)
        self.logger.info("[REEL] Comment panel was already open or has no toggle")

    async def _load_all_comments(
        self, page: Any, reel_url: str, *, like_replies: bool
    ) -> list[FacebookCommentSnapshot]:
        discovered: dict[str, FacebookCommentSnapshot] = {}
        no_new_rounds = 0
        for _round in range(1, self.max_scroll_rounds + 1):
            before = len(discovered)
            for comment in await self._discover_comments(
                page, reel_url, like_replies=like_replies
            ):
                discovered.setdefault(comment.identity_key, comment)
            await self._expand_comment_controls(page, reel_url, like_replies=like_replies)
            await self._scroll_comment_region(page, reel_url)
            await self._bounded_delay(0.35, 0.7)
            for comment in await self._discover_comments(
                page, reel_url, like_replies=like_replies
            ):
                discovered.setdefault(comment.identity_key, comment)
            if len(discovered) == before:
                no_new_rounds += 1
                if no_new_rounds >= self.max_no_new_comment_rounds:
                    break
            else:
                no_new_rounds = 0
        return list(discovered.values())

    async def _expand_comment_controls(
        self, page: Any, reel_url: str, *, like_replies: bool
    ) -> int:
        scope = await self._active_reel_scope(page, reel_url)
        if scope is None:
            return 0
        controls = scope.locator(self._EXPAND_SELECTOR)
        clicked = 0
        for index in range(min(await controls.count(), 120)):
            control = controls.nth(index)
            try:
                if not await control.is_visible():
                    continue
                text = self._normalize_text(
                    " ".join(
                        part
                        for part in (
                            (await control.get_attribute("aria-label")) or "",
                            (await control.inner_text()) or "",
                        )
                        if part
                    )
                )
                if not self._is_expand_control(text, like_replies=like_replies):
                    continue
                await control.click(timeout=self.click_timeout_ms)
                clicked += 1
                await self._bounded_delay(0.1, 0.3)
            except Exception as exc:
                self.logger.debug("Comment expander disappeared: %s", exc)
        return clicked

    async def _scroll_comment_region(self, page: Any, reel_url: str) -> None:
        if not await self._resolve_active_reel_context(page, reel_url):
            return
        await page.evaluate(
            r"""({scopeAttribute}) => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const root = [...document.querySelectorAll(`[${scopeAttribute}]`)]
                    .find(visible);
                if (!root) return false;
                const regions = [root, ...root.querySelectorAll(
                    '[role="dialog"], [role="complementary"], [role="tabpanel"], ' +
                    '[aria-label*="comments" i], [aria-label*="bình luận" i]'
                )].filter(visible);
                const scrollables = regions.flatMap((region) => [
                    region, ...region.querySelectorAll('div')
                ]).filter((el) => {
                    const style = window.getComputedStyle(el);
                    return visible(el) && el.scrollHeight > el.clientHeight &&
                        ['auto', 'scroll'].includes(style.overflowY);
                });
                const target = scrollables.sort(
                    (a, b) => b.scrollHeight - a.scrollHeight
                )[0];
                if (target) {
                    target.scrollTop = target.scrollHeight;
                    return true;
                }
                window.scrollBy(0, Math.max(500, window.innerHeight * 0.8));
                return false;
            }""",
            {"scopeAttribute": self._REEL_SCOPE_ATTRIBUTE},
        )

    async def _discover_comments(
        self, page: Any, reel_url: str, *, like_replies: bool
    ) -> list[FacebookCommentSnapshot]:
        if not await self._resolve_active_reel_context(page, reel_url):
            return []
        raw_comments = await page.evaluate(
            r"""({includeReplies, domAttribute, scopeAttribute}) => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                };
                const commentLike = (el) => {
                    const label = el.getAttribute('aria-label') || '';
                    if (/comment by|bình luận của/i.test(label) ||
                        el.matches('[data-commentid], [data-comment-id]')) {
                        return true;
                    }
                    return [...el.querySelectorAll(
                        'a[href*="comment_id="], a[href*="reply_comment_id="]'
                    )].some((link) =>
                        link.closest('[role="article"]') === el
                    );
                };
                const root = [...document.querySelectorAll(`[${scopeAttribute}]`)]
                    .find(visible);
                if (!root) return [];
                let candidates = [...root.querySelectorAll('[role="article"]')]
                    .filter((el) => visible(el) && commentLike(el));
                if (!candidates.length) {
                    candidates = [...root.querySelectorAll(
                        '[data-commentid], [data-comment-id], div[aria-label*="comment by" i], div[aria-label*="bình luận của" i]'
                    )].filter(visible);
                }
                if (!candidates.length) {
                    const replyText = ['reply', 'phản hồi', 'trả lời', 'like', 'thích'];
                    const links = [...root.querySelectorAll('div[role="button"], span, a')].filter(el => {
                        const txt = (el.innerText || '').trim().toLowerCase();
                        return visible(el) && replyText.includes(txt);
                    });
                    candidates = links.map(el => {
                        let parent = el.parentElement;
                        for(let i=0; i<8 && parent; i++) {
                            const hasLink = parent.querySelector('a[href*="/"]');
                            const hasText = parent.querySelector('[dir="auto"]') || parent.querySelector('span');
                            if(hasLink && hasText && parent.innerText.length > 5 && !parent.innerText.includes('Follow')) {
                                return parent;
                            }
                            parent = parent.parentElement;
                        }
                        return null;
                    }).filter(Boolean);
                    // Keep outermost containers only among candidates
                    candidates = [...new Set(candidates)].filter(c => {
                        return !candidates.some(other => other !== c && other.contains(c));
                    });
                }
                let sequence = 0;
                return candidates.map((container) => {
                    const hrefs = [...container.querySelectorAll('a[href]')];
                    const permalink = hrefs.map((a) => a.href || a.getAttribute('href') || '')
                        .find((href) => /(?:comment_id|reply_comment_id)=/i.test(href)) || '';
                    const ancestorArticle = container.parentElement?.closest(
                        '[role="article"], [data-commentid], [data-comment-id]'
                    );
                    const nested = !!ancestorArticle && commentLike(ancestorArticle);
                    const ariaLevel = Number(container.getAttribute('aria-level') || '1');
                    const isReply = nested || ariaLevel > 1 || /reply_comment_id=/i.test(permalink);
                    if (isReply && !includeReplies) return null;

                    const profile = hrefs.find((link) => {
                        const href = link.getAttribute('href') || '';
                        const text = (link.innerText || link.getAttribute('aria-label') || '').trim();
                        return text && /(facebook\.com|^\/)/i.test(href) &&
                            !/(?:comment_id|reply_comment_id)=|\/(reel|watch|videos)\//i.test(href) &&
                            !/^\d+[smhdw]|^\d+\s*(phút|giờ|ngày|tuần)/i.test(text) &&
                            !/^(like|thích|reply|phản hồi|share|chia sẻ)$/i.test(text);
                    }) || null;
                    const authorName = profile ?
                        (profile.innerText || profile.getAttribute('aria-label') || '').trim() : '';
                    const messageNodes = [
                        ...container.querySelectorAll(
                            '[data-ad-preview="message"], ' +
                            '[data-ad-comet-preview="message"], ' +
                            'span[dir="auto"], div[dir="auto"]'
                        )
                    ].filter((node) => {
                        if (!visible(node) || node.closest('a') || node.querySelector('[dir="auto"]')) return false;
                        const text = (node.innerText || '').trim();
                        return text && text !== authorName &&
                            !/^(like|thích|reply|phản hồi|share|chia sẻ|see more|xem thêm)$/i.test(text) &&
                            !/^\d+[smhdw]|^\d+\s*(phút|giờ|ngày|tuần)/i.test(text);
                    });
                    const text = messageNodes.length ? (messageNodes[0].innerText || '').trim() : '';
                    let commentId = container.getAttribute('data-commentid') ||
                        container.getAttribute('data-comment-id') || null;
                    if (!commentId && permalink) {
                        const match = permalink.match(/[?&](?:comment_id|reply_comment_id)=([^&#]+)/i);
                        commentId = match ? decodeURIComponent(match[1]) : null;
                    }
                    if (!commentId) {
                        const dataFt = container.getAttribute('data-ft') || '';
                        const match = dataFt.match(/"(?:comment_id|reply_comment_id)"\s*:\s*"?(\d+)/i);
                        commentId = match ? match[1] : null;
                    }
                    let domKey = container.getAttribute(domAttribute);
                    if (!domKey) {
                        sequence += 1;
                        domKey = `comment-${Date.now()}-${sequence}`;
                        container.setAttribute(domAttribute, domKey);
                    }
                    const hovercard = profile ? (
                        profile.getAttribute('data-hovercard') ||
                        profile.getAttribute('data-hovercard-prefer-more-content-show') || ''
                    ) : '';
                    const actorMatch = hovercard.match(/[?&](?:id|profile_id)=(\d+)/i) ||
                        (profile ? profile.href.match(/\/people\/[^/]+\/(\d+)/i) : null);
                    return {
                        dom_key: domKey,
                        comment_id: commentId,
                        text,
                        is_reply: isReply,
                        author: {
                            name: authorName,
                            id: actorMatch ? actorMatch[1] : null,
                            url: profile ? (profile.href || profile.getAttribute('href')) : null,
                        },
                    };
                }).filter(Boolean);
            }""",
            {
                "includeReplies": bool(like_replies),
                "domAttribute": self._COMMENT_DOM_ATTRIBUTE,
                "scopeAttribute": self._REEL_SCOPE_ATTRIBUTE,
            },
        )
        comments: list[FacebookCommentSnapshot] = []
        for raw in raw_comments if isinstance(raw_comments, list) else []:
            if not isinstance(raw, dict):
                continue
            author_raw = raw.get("author") if isinstance(raw.get("author"), dict) else {}
            author = self._actor_identity(
                name=str(author_raw.get("name") or ""),
                actor_id=str(author_raw.get("id") or "") or None,
                profile_url=str(author_raw.get("url") or "") or None,
            )
            text = str(raw.get("text") or "").strip()
            comment_id = str(raw.get("comment_id") or "").strip() or None
            dom_key = str(raw.get("dom_key") or "").strip()
            if not dom_key or (not text and not comment_id):
                continue
            identity_key = self._comment_identity(comment_id, author, text)
            comments.append(
                FacebookCommentSnapshot(
                    identity_key=identity_key,
                    dom_key=dom_key,
                    author=author,
                    text=text,
                    comment_id=comment_id,
                    is_reply=bool(raw.get("is_reply")),
                )
            )
        return comments

    async def _process_comments(
        self,
        page: Any,
        reel_url: str,
        comments: list[FacebookCommentSnapshot],
        reel_author: FacebookActorIdentity,
        *,
        like_replies: bool,
        result: dict[str, Any],
    ) -> None:
        processed_comment_ids: set[str] = set()
        if comments and not self._has_actor_identity(reel_author):
            self._record_error(
                result, "Reel author could not be identified safely"
            )
            result["comments_processed"] = len(comments)
            result["failed"] += len(comments)
            self.logger.warning(
                "[COMMENT] Reel author unknown -> no comment reactions were clicked"
            )
            return

        for snapshot in comments:
            if snapshot.identity_key in processed_comment_ids:
                continue
            processed_comment_ids.add(snapshot.identity_key)
            result["comments_processed"] += 1
            author_name = snapshot.author.name or "UNKNOWN"
            try:
                fresh = await self._refresh_comment(
                    page, reel_url, snapshot, like_replies=like_replies
                )
                if fresh is None:
                    raise RuntimeError("comment detached or no longer rendered")
                if not self._has_actor_identity(fresh.author):
                    raise RuntimeError(
                        "comment author could not be identified safely"
                    )
                if self._same_actor(reel_author, fresh.author):
                    result["author_comments_skipped"] += 1
                    self.logger.info(
                        "[COMMENT] %s | Reel author -> SKIP", author_name
                    )
                    continue

                button, state = await self._find_comment_reaction_button(page, fresh)
                if state == "liked":
                    result["comments_already_liked"] += 1
                    self.logger.info(
                        "[COMMENT] %s | Already liked -> SKIP", author_name
                    )
                    continue
                if button is None or state != "not_liked":
                    raise RuntimeError("comment Like state could not be determined safely")

                await button.click(timeout=self.click_timeout_ms)
                await self._bounded_delay()
                verified = await self._refresh_comment(
                    page, reel_url, fresh, like_replies=like_replies
                )
                if verified is None:
                    raise RuntimeError("comment detached before Like verification")
                _button, verified_state = await self._find_comment_reaction_button(
                    page, verified
                )
                if verified_state != "liked":
                    raise RuntimeError("comment Like click could not be verified")
                result["comments_liked"] += 1
                self.logger.info("[COMMENT] %s | LIKE", author_name)
            except Exception as exc:
                result["failed"] += 1
                self.logger.warning(
                    "[COMMENT] %s | FAILED: %s", author_name, exc
                )

    async def _refresh_comment(
        self,
        page: Any,
        reel_url: str,
        snapshot: FacebookCommentSnapshot,
        *,
        like_replies: bool,
    ) -> FacebookCommentSnapshot | None:
        current = self._comment_locator(page, snapshot.dom_key)
        try:
            if await current.count() and await current.first.is_visible():
                return snapshot
        except Exception:
            pass
        for fresh in await self._discover_comments(page, like_replies=like_replies):
            if fresh.identity_key == snapshot.identity_key:
                return fresh
        return None

    async def _find_comment_reaction_button(
        self, page: Any, snapshot: FacebookCommentSnapshot
    ) -> tuple[Any | None, ReactionState]:
        container = self._comment_locator(page, snapshot.dom_key)
        if not await container.count():
            return None, "unknown"
        controls = container.first.locator(self._REACTION_SELECTOR)
        for index in range(min(await controls.count(), 30)):
            control = controls.nth(index)
            try:
                if not await control.is_visible():
                    continue
                belongs_to_comment = await control.evaluate(
                    r"""(el, {domAttribute}) => {
                        const root = el.closest(`[${domAttribute}]`);
                        if (!root) return false;
                        const commentLike = (node) => {
                            const label = node.getAttribute('aria-label') || '';
                            if (/comment by|bình luận của/i.test(label) ||
                                node.matches('[data-commentid], [data-comment-id]')) {
                                return true;
                            }
                            return [...node.querySelectorAll(
                                'a[href*="comment_id="], ' +
                                'a[href*="reply_comment_id="]'
                            )].some((link) =>
                                link.closest('[role="article"]') === node
                            );
                        };
                        for (let node = el.parentElement;
                             node && node !== root;
                             node = node.parentElement) {
                            if ((node.matches('[role="article"]') ||
                                 node.matches('[data-commentid], [data-comment-id]')) &&
                                commentLike(node)) {
                                return false;
                            }
                        }
                        return true;
                    }""",
                    {"domAttribute": self._COMMENT_DOM_ATTRIBUTE},
                )
                if not belongs_to_comment:
                    continue
                state = await self._reaction_state(control)
                if state != "unknown":
                    return control, state
            except Exception:
                continue
        return None, "unknown"

    def _comment_locator(self, page: Any, dom_key: str) -> Any:
        safe_key = dom_key.replace("\\", "\\\\").replace('"', '\\"')
        return page.locator(
            f'[{self._COMMENT_DOM_ATTRIBUTE}="{safe_key}"]'
        )

    async def _reaction_state(self, control: Any) -> ReactionState:
        label = self._normalize_text(
            (await control.get_attribute("aria-label")) or ""
        )
        text = self._normalize_text((await control.inner_text()) or "")
        pressed = self._normalize_text(
            (await control.get_attribute("aria-pressed")) or ""
        )
        combined = " ".join(part for part in (label, text) if part)

        has_liked_term = any(term in combined for term in self._LIKED_TERMS)
        label_is_not_liked = self._is_not_liked_action(label)
        text_is_not_liked = self._is_not_liked_action(text)
        has_not_liked_term = label_is_not_liked or text_is_not_liked

        # Must contain like-related terms to be considered a reaction button
        if not (has_liked_term or has_not_liked_term):
            return "unknown"

        if pressed == "true" or has_liked_term:
            return "liked"
        if pressed == "false":
            return "not_liked"
        color = ""
        try:
            color = str(
                await control.evaluate("el => window.getComputedStyle(el).color")
            )
        except Exception:
            pass
        if color and self._looks_like_facebook_blue(color):
            return "liked"
        if label_is_not_liked or re.search(r"rgba?\(", color):
            return "not_liked"
        return "unknown"

    async def _bounded_delay(
        self, minimum: float | None = None, maximum: float | None = None
    ) -> None:
        lower = self.action_delay_range[0] if minimum is None else max(0.0, minimum)
        upper = self.action_delay_range[1] if maximum is None else max(lower, maximum)
        await self._sleep(random.uniform(lower, upper))

    async def _resolve_active_reel_context(self, page: Any, reel_url: str) -> bool:
        match = re.search(r'/reel/(\d+)', reel_url)
        target_id = match.group(1) if match else ""

        for attempt in range(1, 4):
            result = await page.evaluate(
                r"""({scopeAttribute, targetUrl, targetId}) => {
                    const visible = (el) => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 &&
                            style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    
                    // If already marked, return true
                    if (document.querySelector(`[${scopeAttribute}="true"]`)) {
                        return [true, null];
                    }

                    for (const old of document.querySelectorAll(`[${scopeAttribute}]`)) {
                        old.removeAttribute(scopeAttribute);
                    }

                    let roots = [];

                    if (targetId) {
                        const links = [...document.querySelectorAll(`a[href*="/reel/${targetId}"]`)].filter(visible);
                        for (const link of links) {
                            const container = link.closest('[role="article"]') || link.closest('[role="dialog"]') || link.closest('[role="main"]') || link.closest('div[data-pagelet]');
                            if (container && !roots.includes(container)) roots.push(container);
                        }
                    }

                    if (roots.length === 0) {
                        const activeDialogs = [...document.querySelectorAll('[role="dialog"]')]
                            .filter((dialog) => visible(dialog) && (
                                dialog.querySelector('video') ||
                                dialog.querySelector('a[href*="/reel/"]')
                            ));
                        if (activeDialogs.length > 0) {
                            roots.push(activeDialogs.at(-1));
                        }
                    }

                    if (roots.length === 0) {
                        const media = [...document.querySelectorAll('video')]
                            .find(visible) || [...document.querySelectorAll(
                                'a[href*="/reel/"]'
                            )].find(visible);
                        let root = media?.closest('[role="article"]') ||
                            media?.closest('main, [role="main"]') || null;
                        if (root) roots.push(root);
                    }

                    let finalRoot = roots.find(r => visible(r));

                    if (finalRoot) {
                        finalRoot.setAttribute(scopeAttribute, 'true');
                        return [true, null];
                    }

                    const login_dialog_detected = !![...document.querySelectorAll('[role="dialog"], div')].find(d => {
                        if (!visible(d)) return false;
                        const text = d.innerText?.toLowerCase() || '';
                        return (text.includes('log in') || text.includes('đăng nhập')) && text.length < 200;
                    });
                    const checkpoint_detected = window.location.href.includes('/checkpoint/') || window.location.href.includes('/login/');
                    const visible_buttons = [...document.querySelectorAll('button, [role="button"]')].filter(visible).length;
                    const candidate_containers = document.querySelectorAll('[role="article"], [role="dialog"]').length;

                    return [false, {
                        current_url: window.location.href,
                        target_reel_url: targetUrl,
                        reel_id: targetId,
                        page_title: document.title,
                        video_count: document.querySelectorAll('video').length,
                        button_count: document.querySelectorAll('button, [role="button"]').length,
                        visible_buttons: visible_buttons,
                        candidate_reel_containers: candidate_containers,
                        login_dialog_detected: login_dialog_detected,
                        checkpoint_detected: checkpoint_detected
                    }];
                }""",
                {"scopeAttribute": self._REEL_SCOPE_ATTRIBUTE, "targetUrl": reel_url, "targetId": target_id},
            )
            
            success = result[0]
            diagnostic = result[1]
            
            if success:
                return True
                
            if attempt < 3:
                self.logger.debug(f"[_resolve_active_reel_context] Attempt {attempt} failed, waiting for UI update...")
                await self._bounded_delay(1.5, 2.5)

        if diagnostic:
            if diagnostic.get("login_dialog_detected"):
                raise RuntimeError("Facebook login dialog detected")
            if diagnostic.get("checkpoint_detected"):
                raise RuntimeError("Facebook checkpoint/verification detected")
            diag_str = ", ".join(f"{k}={v}" for k, v in diagnostic.items())
            raise RuntimeError(f"Active Reel container could not be located. Diagnostic: {diag_str}")
            
        return False


    async def _active_reel_scope(self, page: Any, reel_url: str) -> Any | None:
        if not await self._resolve_active_reel_context(page, reel_url):
            return None
        scope = page.locator(f'[{self._REEL_SCOPE_ATTRIBUTE}="true"]')
        if not await scope.count():
            return None
        return scope.first

    @staticmethod
    def _is_expand_control(text: str, *, like_replies: bool) -> bool:
        comment_patterns = (
            r"(?:view|see|show)\s+(?:\d[\d.,]*\s+)?"
            r"(?:more|previous|earlier|all)\s+comments?",
            r"(?:xem|hiển thị)\s+(?:thêm|các)\s+"
            r"(?:\d[\d.,]*\s+)?bình luận",
            r"(?:xem|hiển thị)\s+\d[\d.,]*\s+bình luận"
            r"(?:\s+(?:trước|trước đó))?",
            r"xem\s+bình luận\s+(?:trước|trước đó)",
        )
        if any(re.search(pattern, text) for pattern in comment_patterns):
            return True
        if not like_replies:
            return False
        reply_patterns = (
            r"(?:view|see|show)\s+(?:\d[\d.,]*\s+)?"
            r"(?:more\s+)?repl(?:y|ies)",
            r"xem\s+(?:thêm\s+)?(?:\d[\d.,]*\s+)?"
            r"(?:phản hồi|câu trả lời)",
        )
        return any(re.search(pattern, text) for pattern in reply_patterns)

    @classmethod
    def _is_not_liked_action(cls, value: str) -> bool:
        if value in cls._NOT_LIKED_TERMS:
            return True
        patterns = (
            r"^like\s+(?:this\s+)?(?:comment|reel|post|video)$",
            r"^thích\s+(?:bình luận|thước phim|bài viết|video)(?:\s+này)?$",
        )
        return any(re.fullmatch(pattern, value) for pattern in patterns)

    @classmethod
    def _actor_identity(
        cls,
        *,
        name: str,
        actor_id: str | None,
        profile_url: str | None,
    ) -> FacebookActorIdentity:
        canonical_url = cls._canonical_profile_url(profile_url)
        return FacebookActorIdentity(
            name=unicodedata.normalize("NFKC", name or "").strip(),
            actor_id=actor_id or cls._actor_id_from_url(canonical_url),
            profile_url=canonical_url,
        )

    @classmethod
    def _same_actor(
        cls, reel_author: FacebookActorIdentity, comment_author: FacebookActorIdentity
    ) -> bool:
        if reel_author.actor_id and comment_author.actor_id:
            return reel_author.actor_id == comment_author.actor_id
        if reel_author.profile_url and comment_author.profile_url:
            return reel_author.profile_url == comment_author.profile_url
        reel_name = cls._normalize_text(reel_author.name)
        comment_name = cls._normalize_text(comment_author.name)
        return bool(reel_name and comment_name and reel_name == comment_name)

    @staticmethod
    def _has_actor_identity(actor: FacebookActorIdentity) -> bool:
        return bool(actor.actor_id or actor.profile_url or actor.name.strip())

    @classmethod
    def _comment_identity(
        cls,
        comment_id: str | None,
        author: FacebookActorIdentity,
        text: str,
    ) -> str:
        if comment_id:
            return f"comment:{comment_id}"
        material = "\n".join(
            (
                author.actor_id or "",
                author.profile_url or "",
                cls._normalize_text(author.name),
                cls._normalize_text(text),
            )
        )
        return "fingerprint:" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical_profile_url(url: str | None) -> str | None:
        if not url:
            return None
        absolute = urljoin("https://www.facebook.com/", url.strip())
        parts = urlsplit(absolute)
        if "facebook.com" not in parts.netloc.casefold():
            return None
        path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
        query = parse_qs(parts.query)
        identity_query = ""
        if path.casefold().endswith("/profile.php") and query.get("id"):
            identity_query = f"id={query['id'][0]}"
        return urlunsplit(("https", "www.facebook.com", path, identity_query, ""))

    @staticmethod
    def _actor_id_from_url(url: str | None) -> str | None:
        if not url:
            return None
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        for key in ("id", "profile_id"):
            value = query.get(key, [None])[0]
            if value and str(value).isdigit():
                return str(value)
        match = re.search(r"/(?:people/[^/]+/)?(\d+)(?:/|$)", parts.path)
        return match.group(1) if match else None

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value or "").casefold()
        return " ".join(normalized.split())

    @staticmethod
    def _looks_like_facebook_blue(color: str) -> bool:
        match = re.search(
            r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", color
        )
        if not match:
            return False
        red, green, blue = (int(value) for value in match.groups())
        return blue >= 150 and blue >= red + 45 and blue >= green + 35

    @staticmethod
    def _record_error(result: dict[str, Any], message: str) -> None:
        result["success"] = False
        existing = str(result.get("error") or "").strip()
        result["error"] = f"{existing}; {message}" if existing else message

    def _log_summary(self, result: dict[str, Any]) -> None:
        self.logger.info(
            "===== REEL ENGAGEMENT SUMMARY =====\n\n"
            "Reel:\nLiked: %s\n\n"
            "Comments discovered: %s\nComments processed: %s\n\n"
            "Liked: %s\nAlready liked: %s\nSkipped reel author: %s\nFailed: %s",
            "YES" if result.get("reel_liked") else "NO",
            result.get("comments_found", 0),
            result.get("comments_processed", 0),
            result.get("comments_liked", 0),
            result.get("comments_already_liked", 0),
            result.get("author_comments_skipped", 0),
            result.get("failed", 0),
        )


__all__ = [
    "FacebookActorIdentity",
    "FacebookCommentSnapshot",
    "FacebookReelEngagementService",
]
