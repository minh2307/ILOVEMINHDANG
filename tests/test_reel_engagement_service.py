from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.browser.facebook_browser_worker import FacebookBrowserWorker
from app.browser.facebook_browser_manager import FacebookBrowserManager
from app.browser.facebook_job import FacebookJobStore
from app.config.facebook_browser import FacebookBrowserConfig
from app.infrastructure.facebook.reel_engagement_service import (
    FacebookActorIdentity,
    FacebookCommentSnapshot,
    FacebookReelEngagementService,
)


REEL_URL = "https://www.facebook.com/reel/123"
REEL_AUTHOR = FacebookActorIdentity(
    name="RoboLearn AI",
    actor_id="100",
    profile_url="https://www.facebook.com/robolearn",
)
OTHER_AUTHOR = FacebookActorIdentity(
    name="Nguyễn Văn A",
    actor_id="200",
    profile_url="https://www.facebook.com/nguyenvana",
)


class FakePage:
    url = REEL_URL

    def is_closed(self) -> bool:
        return False


class StateButton:
    def __init__(self, on_click: Any) -> None:
        self.clicks = 0
        self._on_click = on_click

    async def click(self, **_kwargs: Any) -> None:
        self.clicks += 1
        self._on_click()


class ReactionControl:
    def __init__(
        self,
        *,
        label: str = "",
        text: str = "",
        pressed: str | None = None,
        color: str = "rgb(0, 0, 0)",
    ) -> None:
        self._attributes = {"aria-label": label, "aria-pressed": pressed}
        self._text = text
        self._color = color

    async def get_attribute(self, name: str) -> str | None:
        return self._attributes.get(name)

    async def inner_text(self) -> str:
        return self._text

    async def evaluate(self, _script: str) -> str:
        return self._color


def comment(
    comment_id: str,
    author: FacebookActorIdentity = OTHER_AUTHOR,
    text: str = "Hay quá",
) -> FacebookCommentSnapshot:
    return FacebookCommentSnapshot(
        identity_key=f"comment:{comment_id}",
        dom_key=f"dom-{comment_id}",
        author=author,
        text=text,
        comment_id=comment_id,
    )


class EngagementHarness(FacebookReelEngagementService):
    def __init__(
        self,
        *,
        reel_state: str = "liked",
        comments: list[FacebookCommentSnapshot] | None = None,
        comment_states: dict[str, str] | None = None,
        detached: set[str] | None = None,
    ) -> None:
        async def no_sleep(_seconds: float) -> None:
            return None

        super().__init__(
            action_delay_min_seconds=0,
            action_delay_max_seconds=0,
            sleeper=no_sleep,
        )
        self.reel_state = reel_state
        self.comments = list(comments or [])
        self.comment_states = dict(comment_states or {})
        self.detached = set(detached or set())
        self.reel_button = StateButton(self._set_reel_liked)
        self.comment_buttons: dict[str, StateButton] = {}

    def _set_reel_liked(self) -> None:
        self.reel_state = "liked"

    async def _navigate(self, page: Any, reel_url: str) -> None:
        page.url = reel_url

    async def _ensure_authenticated(self, page: Any) -> None:
        return None

    async def _dismiss_blocking_popups(self, page: Any) -> None:
        return None

    async def _extract_reel_author(self, page: Any) -> FacebookActorIdentity:
        return REEL_AUTHOR

    async def _find_reel_reaction_button(self, page: Any):
        return self.reel_button, self.reel_state

    async def _open_comment_panel(self, page: Any) -> None:
        return None

    async def _load_all_comments(self, page: Any, *, like_replies: bool):
        return self.comments

    async def _refresh_comment(
        self,
        page: Any,
        snapshot: FacebookCommentSnapshot,
        *,
        like_replies: bool,
    ) -> FacebookCommentSnapshot | None:
        if snapshot.identity_key in self.detached:
            return None
        return snapshot

    async def _find_comment_reaction_button(
        self, page: Any, snapshot: FacebookCommentSnapshot
    ):
        state = self.comment_states.get(snapshot.identity_key, "not_liked")
        if state != "not_liked":
            return None, state

        def mark_liked() -> None:
            self.comment_states[snapshot.identity_key] = "liked"

        button = self.comment_buttons.setdefault(
            snapshot.identity_key, StateButton(mark_liked)
        )
        return button, state


@pytest.mark.asyncio
async def test_unliked_reel_is_liked_and_verified() -> None:
    service = EngagementHarness(reel_state="not_liked")

    result = await service.like_reel_and_comments(
        FakePage(), REEL_URL, like_comments=False
    )

    assert result["reel_liked"] is True
    assert result["reel_like_clicked"] is True
    assert service.reel_button.clicks == 1


@pytest.mark.asyncio
async def test_already_liked_reel_is_never_clicked_or_unliked() -> None:
    service = EngagementHarness(reel_state="liked")

    result = await service.like_reel_and_comments(
        FakePage(), REEL_URL, like_comments=False
    )

    assert result["reel_liked"] is True
    assert result["reel_like_clicked"] is False
    assert service.reel_button.clicks == 0


@pytest.mark.asyncio
async def test_other_users_top_level_comment_is_liked() -> None:
    target = comment("1")
    service = EngagementHarness(comments=[target])

    result = await service.like_reel_and_comments(FakePage(), REEL_URL)

    assert result["comments_liked"] == 1
    assert service.comment_buttons[target.identity_key].clicks == 1


@pytest.mark.asyncio
async def test_reel_author_comment_is_skipped_by_actor_id() -> None:
    same_author_with_different_url = FacebookActorIdentity(
        name="Different rendered label",
        actor_id="100",
        profile_url="https://www.facebook.com/profile.php?id=100",
    )
    target = comment("2", same_author_with_different_url, "Source code ở link...")
    service = EngagementHarness(comments=[target])

    result = await service.like_reel_and_comments(FakePage(), REEL_URL)

    assert result["author_comments_skipped"] == 1
    assert result["comments_liked"] == 0
    assert target.identity_key not in service.comment_buttons


@pytest.mark.asyncio
async def test_already_liked_comment_is_skipped_without_click() -> None:
    target = comment("3")
    service = EngagementHarness(
        comments=[target], comment_states={target.identity_key: "liked"}
    )

    result = await service.like_reel_and_comments(FakePage(), REEL_URL)

    assert result["comments_already_liked"] == 1
    assert result["comments_liked"] == 0
    assert target.identity_key not in service.comment_buttons


@pytest.mark.asyncio
async def test_unknown_comment_reaction_state_is_not_clicked() -> None:
    target = comment("unknown-state")
    service = EngagementHarness(
        comments=[target], comment_states={target.identity_key: "unknown"}
    )

    result = await service.like_reel_and_comments(FakePage(), REEL_URL)

    assert result["failed"] == 1
    assert result["comments_liked"] == 0
    assert target.identity_key not in service.comment_buttons


@pytest.mark.asyncio
async def test_duplicate_comment_is_processed_only_once() -> None:
    target = comment("4")
    duplicate_render = replace(target, dom_key="rerendered-dom-4")
    service = EngagementHarness(comments=[target, duplicate_render])

    result = await service.like_reel_and_comments(FakePage(), REEL_URL)

    assert result["comments_found"] == 2
    assert result["comments_processed"] == 1
    assert result["comments_liked"] == 1
    assert service.comment_buttons[target.identity_key].clicks == 1


@pytest.mark.asyncio
async def test_detached_comment_fails_in_isolation_and_next_comment_continues() -> None:
    detached = comment("5", text="Deleted while processing")
    healthy = comment("6", text="Still here")
    service = EngagementHarness(
        comments=[detached, healthy], detached={detached.identity_key}
    )

    result = await service.like_reel_and_comments(FakePage(), REEL_URL)

    assert result["failed"] == 1
    assert result["comments_liked"] == 1
    assert service.comment_buttons[healthy.identity_key].clicks == 1


@pytest.mark.asyncio
async def test_reel_without_comments_completes_without_error() -> None:
    service = EngagementHarness(comments=[])

    result = await service.like_reel_and_comments(FakePage(), REEL_URL)

    assert result["success"] is True
    assert result["comments_found"] == 0
    assert result["comments_processed"] == 0
    assert result["failed"] == 0


@pytest.mark.asyncio
async def test_comment_with_unknown_author_is_not_clicked() -> None:
    unknown_author = FacebookActorIdentity()
    target = comment("unknown-author", author=unknown_author)
    service = EngagementHarness(comments=[target])

    result = await service.like_reel_and_comments(FakePage(), REEL_URL)

    assert result["failed"] == 1
    assert result["comments_liked"] == 0
    assert target.identity_key not in service.comment_buttons


@pytest.mark.parametrize(
    ("label", "like_replies", "expected"),
    [
        ("View 12 more comments", False, True),
        ("View previous comments", False, True),
        ("Xem thêm 12 bình luận", False, True),
        ("Xem bình luận trước", False, True),
        ("View 3 more replies", False, False),
        ("View 3 more replies", True, True),
        ("Xem thêm 3 phản hồi", True, True),
    ],
)
def test_comment_expander_labels_are_bilingual_and_reply_aware(
    label: str, like_replies: bool, expected: bool
) -> None:
    normalized = FacebookReelEngagementService._normalize_text(label)

    assert FacebookReelEngagementService._is_expand_control(
        normalized, like_replies=like_replies
    ) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control", "expected"),
    [
        (ReactionControl(label="Like this comment", pressed="false"), "not_liked"),
        (ReactionControl(label="Thích bình luận này", pressed="false"), "not_liked"),
        (ReactionControl(label="15 likes", pressed="false"), "unknown"),
        (ReactionControl(label="Gỡ lượt thích"), "liked"),
    ],
)
async def test_reaction_state_uses_explicit_accessible_actions_only(
    control: ReactionControl, expected: str
) -> None:
    service = FacebookReelEngagementService()

    assert await service._reaction_state(control) == expected


@pytest.mark.asyncio
async def test_worker_api_routes_engagement_to_existing_worker_page(
    tmp_path: Path,
) -> None:
    config = replace(
        FacebookBrowserConfig.load(),
        profile_path=tmp_path / "profile",
        executable_path=tmp_path / "chrome",
        lock_path=tmp_path / "locks" / "facebook.lock",
        pid_path=tmp_path / "pids" / "chrome.pid",
        diagnostics_path=tmp_path / "diagnostics",
        downloads_path=tmp_path / "downloads",
        queue_database_path=tmp_path / "jobs.sqlite3",
        lock_timeout_seconds=1,
        lock_wait_timeout_seconds=1,
        lock_retry_interval_seconds=0.001,
    )
    page = FakePage()

    class Tabs:
        async def get(self, *_args: Any, **_kwargs: Any) -> FakePage:
            return page

        async def release_job(self, _job_id: str) -> None:
            return None

    manager = SimpleNamespace(
        tabs=Tabs(),
        browser_process_id=42,
        start=AsyncMock(),
    )
    expected = {"reel_liked": True, "comments_found": 0}
    engagement = SimpleNamespace(
        like_reel_and_comments=AsyncMock(return_value=expected)
    )
    worker = FacebookBrowserWorker(
        manager=manager,
        store=FacebookJobStore(config.queue_database_path),
        config=config,
        engagement_service=engagement,
    )

    result = await worker.like_reel_and_comments(REEL_URL, like_replies=False)

    assert result == expected
    engagement.like_reel_and_comments.assert_awaited_once_with(
        page,
        REEL_URL,
        like_reel=True,
        like_comments=True,
        like_replies=False,
    )


def test_actor_comparison_does_not_fall_back_to_same_name_when_ids_differ() -> None:
    first = FacebookActorIdentity("Same Name", "100", None)
    second = FacebookActorIdentity("Same Name", "200", None)

    assert FacebookReelEngagementService._same_actor(first, second) is False


def test_worker_reuses_browser_managers_canonical_file_lock(tmp_path: Path) -> None:
    config = replace(
        FacebookBrowserConfig.load(),
        profile_path=tmp_path / "profile",
        executable_path=tmp_path / "chrome",
        lock_path=tmp_path / "locks" / "facebook.lock",
        pid_path=tmp_path / "pids" / "chrome.pid",
        diagnostics_path=tmp_path / "diagnostics",
        downloads_path=tmp_path / "downloads",
        queue_database_path=tmp_path / "jobs.sqlite3",
    )
    manager = FacebookBrowserManager(config=config)
    worker = FacebookBrowserWorker(
        manager=manager,
        store=FacebookJobStore(config.queue_database_path),
        config=config,
    )

    assert worker._file_lock is manager.browser_lock
