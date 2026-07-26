from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.browser.cdha_client import CDHAWebClient
from app.browser.selector_resolver import SelectorResolver
from app.config.settings import Settings
from app.errors import CDHAUploadError, FrameNotReadyError


def make_settings(tmp_path: Path) -> Settings:
    return replace(
        Settings.from_env(env_file=tmp_path / "missing.env"),
        database_path=tmp_path / "jobs.sqlite3",
        job_data_dir=tmp_path / "jobs",
        chrome_profile_dir=tmp_path / "profile",
        max_cdha_retries=2,
        retry_initial_delay_seconds=0.0,
        retry_max_delay_seconds=0.0,
        retry_jitter_seconds=0.0,
    )


class ClickLocator:
    def __init__(self) -> None:
        self.clicked = 0
        self.files: list[str] = []

    async def click(self, **_: Any) -> None:
        self.clicked += 1

    async def set_input_files(self, value: str) -> None:
        self.files.append(value)

    async def is_enabled(self) -> bool:
        return True


class DialogResolver:
    def __init__(self) -> None:
        self.zone = ClickLocator()
        self.complete = ClickLocator()

    async def find_first(self, page: Any, key: str, **_: Any) -> Any:
        if key == "cdha.upload_zone":
            return self.zone
        if key == "cdha.upload_complete_button":
            return self.complete
        raise AssertionError(key)

    async def exists(self, page: Any, key: str, **_: Any) -> bool:
        return False


class DialogClient(CDHAWebClient):
    def __init__(self, *args: Any, ready: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.ready = ready
        self.resolve_calls = 0
        self.file_input = ClickLocator()

    async def _resolve_upload_file_input(self, page: Any, *, timeout_ms: int = 2_000) -> Any:
        self.resolve_calls += 1
        if not self.ready and self.resolve_calls == 1:
            raise FrameNotReadyError("slow frame")
        return self.file_input


@pytest.mark.parametrize("ready,expected_clicks", [(True, 0), (False, 1)])
def test_upload_dialog_is_not_clicked_when_file_input_is_ready(
    tmp_path: Path, ready: bool, expected_clicks: int
) -> None:
    resolver = DialogResolver()
    client = DialogClient(
        make_settings(tmp_path), object(), object(), resolver=resolver, ready=ready
    )
    result = asyncio.run(client._ensure_upload_dialog_open(
        object(), job_id="j", diagnostics_dir=tmp_path
    ))
    assert result is client.file_input
    assert resolver.zone.clicked == expected_clicks


class ReconciliationClient(CDHAWebClient):
    async def _reconcile_existing_upload(self, page: Any, video: Path) -> str:
        return "uncertain"


def test_uncertain_partial_upload_is_never_repeated(tmp_path: Path) -> None:
    file_input = ClickLocator()
    client = ReconciliationClient(make_settings(tmp_path), object(), object(), resolver=DialogResolver())
    with pytest.raises(CDHAUploadError) as caught:
        asyncio.run(client._upload_video_file(object(), file_input, tmp_path / "video.mp4"))
    assert caught.value.error_code == "CDHA_UPLOAD_UNCERTAIN"
    assert caught.value.retryable is False
    assert caught.value.manual_action_required is True
    assert file_input.files == []


class AcknowledgementResolver(DialogResolver):
    async def exists(self, page: Any, key: str, **_: Any) -> bool:
        return key == "cdha.upload_started"


class AcknowledgementClient(CDHAWebClient):
    async def _view_url_value(self, page: Any) -> str:
        return ""


def test_upload_acknowledgement_uses_state_without_fixed_sleep(tmp_path: Path) -> None:
    client = AcknowledgementClient(
        make_settings(tmp_path), object(), object(), resolver=AcknowledgementResolver()
    )
    asyncio.run(client._wait_for_upload_acknowledgement(object()))


class CompleteClient(AcknowledgementClient):
    async def _resolve_upload_frame(self, page: Any, *, timeout_ms: int = 2_000) -> Any:
        return object()

    async def _wait_for_upload(self, page: Any) -> None:
        raise CDHAUploadError(
            "uncertain", error_code="CDHA_UPLOAD_UNCERTAIN", retryable=False
        )


def test_complete_click_is_not_retried_when_outcome_is_uncertain(tmp_path: Path) -> None:
    resolver = DialogResolver()
    client = CompleteClient(make_settings(tmp_path), object(), object(), resolver=resolver)
    with pytest.raises(CDHAUploadError):
        asyncio.run(client._complete_upload(object()))
    assert resolver.complete.clicked == 1


def test_result_container_has_no_body_fallback(tmp_path: Path) -> None:
    resolver = SelectorResolver(make_settings(tmp_path).selectors_path)
    rendered = str(resolver.candidates("cdha.result_container"))
    assert "'body'" not in rendered
    assert '"body"' not in rendered


class _FrameLocator:
    @property
    def first(self):
        return self

    async def wait_for(self, **_: Any) -> None:
        return None


class _ReloadingFramePage:
    def __init__(self) -> None:
        self.frames: list[object] = []

    def locator(self, _selector: str) -> _FrameLocator:
        return _FrameLocator()

    def frame_locator(self, _selector: str) -> object:
        frame = object()
        self.frames.append(frame)
        return frame


def test_upload_frame_is_resolved_fresh_after_reload(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = CDHAWebClient(settings, object(), object(), resolver=SelectorResolver(settings.selectors_path))
    page = _ReloadingFramePage()
    first = asyncio.run(client._resolve_upload_frame(page))
    second = asyncio.run(client._resolve_upload_frame(page))
    assert first is not second
    assert page.frames == [first, second]


class _ExistingViewClient(CDHAWebClient):
    async def _view_url_value(self, page: Any) -> str:
        return "https://cdha.ai/view/known"


def test_existing_view_url_skips_complete_side_effect(tmp_path: Path) -> None:
    resolver = DialogResolver()
    client = _ExistingViewClient(make_settings(tmp_path), object(), object(), resolver=resolver)
    asyncio.run(client._complete_upload(object()))
    assert resolver.complete.clicked == 0


def test_missing_upload_acknowledgement_is_uncertain(tmp_path: Path) -> None:
    settings = replace(make_settings(tmp_path), page_timeout_seconds=0)
    client = AcknowledgementClient(settings, object(), object(), resolver=DialogResolver())
    with pytest.raises(CDHAUploadError) as caught:
        asyncio.run(client._wait_for_upload_acknowledgement(object()))
    assert caught.value.error_code == "CDHA_UPLOAD_UNCERTAIN"
    assert caught.value.manual_action_required is True
