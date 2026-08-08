from __future__ import annotations

import asyncio
import html
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.browser.cdha_client import CDHAWebClient
from app.browser.gemini_client import GeminiWebClient
from app.browser.selector_resolver import SelectorResolutionError, SelectorResolver
from app.config.settings import Settings
from app.main import build_parser
from app.models.results import CDHAAnalysisResult
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.screenshot_service import SCREENSHOT_SECTIONS, ScreenshotService


FIXTURES = Path(__file__).parent / "fixtures"


def make_settings(tmp_path: Path, **changes: Any) -> Settings:
    settings = replace(
        Settings.from_env(env_file=tmp_path / "missing.env"),
        database_path=tmp_path / "jobs.sqlite3",
        job_data_dir=tmp_path / "jobs",
        chrome_profile_dir=tmp_path / "profile",
    )
    return replace(settings, **changes)


class WaitLocator:
    def __init__(self, key: tuple[Any, ...], available: set[tuple[Any, ...]], calls: list[Any]):
        self.key = key
        self.available = available
        self.calls = calls

    @property
    def first(self) -> "WaitLocator":
        return self

    async def wait_for(self, **_: Any) -> None:
        self.calls.append(self.key)
        if self.key not in self.available:
            raise TimeoutError(str(self.key))


class SelectorPage:
    url = "https://fixture.invalid"

    def __init__(self, available: set[tuple[Any, ...]]):
        self.available = available
        self.calls: list[Any] = []

    def locator(self, selector: str) -> WaitLocator:
        return WaitLocator(("css", selector), self.available, self.calls)

    def get_by_role(self, role: str, *, name: str | None, exact: bool) -> WaitLocator:
        return WaitLocator(("role", role, name), self.available, self.calls)

    def get_by_text(self, text: str, *, exact: bool) -> WaitLocator:
        return WaitLocator(("text", text), self.available, self.calls)

    async def title(self) -> str:
        return "Synthetic page"

    async def content(self) -> str:
        return "<html><body>Synthetic page</body></html>"

    async def screenshot(self, *, path: str, **_: Any) -> None:
        Path(path).write_bytes(b"png")


def test_gemini_selector_fallback_uses_role_and_accessible_name(tmp_path: Path) -> None:
    config = tmp_path / "selectors.yaml"
    config.write_text(
        "gemini:\n  prompt:\n    - css: '.missing'\n    - role: textbox\n      name: Prompt\n",
        encoding="utf-8",
    )
    resolver = SelectorResolver(config)
    page = SelectorPage({("role", "textbox", "Prompt")})

    locator = asyncio.run(resolver.find_first(page, "gemini.prompt"))

    assert locator.key == ("role", "textbox", "Prompt")
    assert page.calls == [("css", ".missing"), ("role", "textbox", "Prompt")]


def test_cdha_selector_fallback_uses_visible_text(tmp_path: Path) -> None:
    config = tmp_path / "selectors.yaml"
    config.write_text(
        "cdha:\n  consultation:\n    - css: '.missing'\n    - text: Hội chẩn\n",
        encoding="utf-8",
    )
    resolver = SelectorResolver(config)
    page = SelectorPage({("text", "Hội chẩn")})

    locator = asyncio.run(resolver.find_first(page, "cdha.consultation"))

    assert locator.key == ("text", "Hội chẩn")


def test_selector_failure_creates_metadata_and_screenshot_diagnostics(tmp_path: Path) -> None:
    config = tmp_path / "selectors.yaml"
    config.write_text("gemini:\n  prompt:\n    - css: '.missing'\n", encoding="utf-8")
    resolver = SelectorResolver(config)
    page = SelectorPage(set())

    with pytest.raises(SelectorResolutionError):
        asyncio.run(
            resolver.find_first(
                page, "gemini.prompt", diagnostics_dir=tmp_path / "diagnostics"
            )
        )

    assert (tmp_path / "diagnostics" / "selector-gemini-prompt.png").is_file()
    assert (tmp_path / "diagnostics" / "selector-gemini-prompt.json").is_file()
    assert not (tmp_path / "diagnostics" / "selector-gemini-prompt.html").exists()


class TextLocator:
    def __init__(self, text: str):
        self.text = text

    async def inner_text(self) -> str:
        return self.text


class KeyResolver:
    def __init__(self, present: set[str] | None = None, texts: dict[str, str] | None = None):
        self.present = present or set()
        self.texts = texts or {}

    async def exists(self, page: Any, key: str, **_: Any) -> bool:
        return key in self.present

    async def find_first(self, page: Any, key: str, **_: Any) -> TextLocator:
        if key not in self.texts:
            raise SelectorResolutionError(key)
        return TextLocator(self.texts[key])


def test_gemini_login_and_security_detection(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = GeminiWebClient(
        settings, object(), object(), resolver=KeyResolver({"gemini.login_markers"})
    )
    page = type("Page", (), {"url": "https://gemini.google.com/app"})()

    assert not asyncio.run(client.is_authenticated(page))

    client.resolver = KeyResolver({"gemini.authenticated_marker"})
    assert asyncio.run(client.is_authenticated(page))


def test_cdha_login_and_checkpoint_detection(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = CDHAWebClient(
        settings, object(), object(), resolver=KeyResolver({"cdha.security_markers"})
    )
    page = type("Page", (), {"url": settings.cdha_url})()

    assert not asyncio.run(client.is_authenticated(page))

    client.resolver = KeyResolver({"cdha.authenticated_marker"})
    assert asyncio.run(client.is_authenticated(page))


class TextCollection:
    def __init__(self, values: list[str]):
        self.values = values

    async def count(self) -> int:
        return len(self.values)

    def nth(self, index: int) -> TextLocator:
        return TextLocator(self.values[index])


class GeminiFixturePage:
    def __init__(self, responses: list[str]):
        self.responses = responses

    def locator(self, selector: str) -> TextCollection:
        if selector == '[data-message-author-role="model"]':
            return TextCollection(self.responses)
        return TextCollection([])


def test_gemini_extracts_only_newest_response_from_synthetic_html(tmp_path: Path) -> None:
    source = (FIXTURES / "gemini_response.html").read_text(encoding="utf-8")
    responses = [
        " ".join(html.unescape(re.sub(r"<[^>]+>", "", value)).split())
        for value in re.findall(
            r'<div data-message-author-role="model">(.*?)</div>', source, re.DOTALL
        )
    ]
    settings = make_settings(tmp_path)
    client = GeminiWebClient(
        settings, object(), object(), resolver=SelectorResolver(settings.selectors_path)
    )

    newest = asyncio.run(client._newest_response_text(GeminiFixturePage(responses)))

    assert newest != "Câu trả lời cũ"
    assert "Cơ quan/vùng khảo sát: Gan" in newest


def test_cdha_video_input_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        CDHAWebClient.validate_video_path(tmp_path / "missing.mp4")
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        CDHAWebClient.validate_video_path(empty)
    unsupported = tmp_path / "video.txt"
    unsupported.write_bytes(b"video")
    with pytest.raises(ValueError, match="Unsupported"):
        CDHAWebClient.validate_video_path(unsupported)
    valid = tmp_path / "video.mp4"
    valid.write_bytes(b"video")
    assert CDHAWebClient.validate_video_path(valid) == valid.resolve()


class CDHAWaitPage:
    url = "https://cdha.invalid/upload"

    async def evaluate(self, *_: Any, **__: Any) -> str:
        return ""

def test_cdha_upload_failure_is_reported(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    resolver = KeyResolver(
        {"cdha.upload_error"}, {"cdha.upload_error": "Unsupported video file"}
    )
    client = CDHAWebClient(settings, object(), object(), resolver=resolver)

    with pytest.raises(RuntimeError, match="Unsupported video file"):
        asyncio.run(client._wait_for_upload(CDHAWaitPage()))


def test_cdha_analysis_timeout_is_bounded(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, cdha_analysis_timeout_seconds=0)
    client = CDHAWebClient(settings, object(), object(), resolver=KeyResolver())

    with pytest.raises(TimeoutError, match="did not start"):
        asyncio.run(client._wait_for_analysis(CDHAWaitPage()))


def test_cdha_view_url_is_terminal_without_result_container(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, cdha_result_timeout_seconds=0)
    client = CDHAWebClient(settings, object(), object(), resolver=KeyResolver())
    page = type("Page", (), {"url": "https://cdha.ai/dash?view=44088"})()

    asyncio.run(client._wait_for_analysis(page))


def test_cdha_error_message_stops_analysis(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    resolver = KeyResolver(
        {"cdha.error_message"}, {"cdha.error_message": "Analysis failed safely"}
    )
    client = CDHAWebClient(settings, object(), object(), resolver=resolver)

    with pytest.raises(RuntimeError, match="Analysis failed safely"):
        asyncio.run(client._wait_for_analysis(CDHAWaitPage()))


def fixture_texts() -> dict[str, str]:
    source = (FIXTURES / "cdha_result.html").read_text(encoding="utf-8")
    pairs = re.findall(r'data-key="([^"]+)">(.*?)</(?:main|section)>', source, re.DOTALL)
    return {key: html.unescape(re.sub(r"<[^>]+>", "", value)).strip() for key, value in pairs}


def test_cdha_result_extraction_from_synthetic_html(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = CDHAWebClient(
        settings, object(), object(), resolver=KeyResolver(texts=fixture_texts())
    )

    result = asyncio.run(client.extract_result(object(), "job"))

    assert result.triage == "Ưu tiên cao"
    assert result.confidence == "87%"
    assert result.key_findings == ["Tổn thương giảm âm", "Bờ không đều"]
    assert result.marked_regions == ["Vùng 1", "Vùng 2"]
    assert result.warnings == []


class NestedResultLocator:
    def __init__(self, heading: str, nested_text: str) -> None:
        self.heading = heading
        self.nested_text = nested_text

    async def inner_text(self) -> str:
        return self.heading

    async def evaluate(self, *_: Any) -> str:
        return self.nested_text


class NestedResultResolver:
    async def find_first(self, page: Any, key: str, **_: Any) -> Any:
        values = {
            "cdha.result_container": "Key findings:\nTổn thương giảm âm\nImpression:\nCần đối chiếu",
            "cdha.key_findings": "Key findings:\n• Tổn thương giảm âm\n• Bờ không đều",
            "cdha.impression": "Impression:\nHình ảnh gợi ý tổn thương, cần đối chiếu.",
        }
        if key not in values:
            raise SelectorResolutionError(key)
        heading = "Key findings:" if key == "cdha.key_findings" else "Impression:"
        if key == "cdha.result_container":
            heading = values[key]
        return NestedResultLocator(heading, values[key])


class NestedResultPage:
    url = "https://cdha.ai/dash?view=nested-result"


def test_cdha_result_extraction_reads_nested_value_instead_of_heading(tmp_path: Path) -> None:
    client = CDHAWebClient(
        make_settings(tmp_path), object(), object(), resolver=NestedResultResolver()
    )

    result = asyncio.run(client.extract_result(NestedResultPage(), "job"))

    assert result.key_findings == ["Tổn thương giảm âm", "Bờ không đều"]
    assert result.impression == "Hình ảnh gợi ý tổn thương, cần đối chiếu."
    assert result.analysis_url == NestedResultPage.url
    assert result.raw_key_findings.startswith("Key findings:")
    assert result.raw_impression.startswith("Impression:")


def test_missing_cdha_result_fields_add_warnings(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    client = CDHAWebClient(
        settings,
        object(),
        object(),
        resolver=KeyResolver(texts={"cdha.result_container": "raw"}),
    )

    result = asyncio.run(client.extract_result(object(), "job"))

    assert result.triage is None
    assert result.key_findings == []
    assert len(result.warnings) == 6


def test_cdha_result_model_serializes_paths() -> None:
    result = CDHAAnalysisResult(
        success=True,
        job_id="job",
        key_findings=["finding"],
        analysis_url="https://cdha.ai/dash?view=result",
        raw_key_findings="Key findings:\nfinding",
        raw_impression="Impression:\nvalue",
        result_json_path=Path("result.json"),
        screenshot_paths=[Path("01.png")],
    )

    payload = result.to_dict()
    assert payload["result_json_path"] == "result.json"
    assert payload["screenshot_paths"] == ["01.png"]
    assert payload["key_findings"] == ["finding"]
    assert payload["analysis_url"] == "https://cdha.ai/dash?view=result"
    assert payload["raw_key_findings"] == "Key findings:\nfinding"


class MissingScreenshotResolver:
    async def find_first(self, page: Any, key: str, **_: Any) -> Any:
        raise SelectorResolutionError(key)


class ScreenshotPage:
    async def set_viewport_size(self, *_: Any, **__: Any) -> None:
        return None

    async def add_style_tag(self, *_: Any, **__: Any) -> None:
        return None

    url = "https://cdha.invalid/result"

    async def evaluate(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_function(self, *_: Any, **__: Any) -> None:
        return None

    async def screenshot(self, *, path: str, **_: Any) -> None:
        Path(path).write_bytes(b"full-page-png")

    async def content(self) -> str:
        return "<html><body>diagnostic result</body></html>"


def test_screenshot_missing_elements_use_deterministic_fallbacks(tmp_path: Path) -> None:
    service = ScreenshotService(MissingScreenshotResolver())

    paths, warnings = asyncio.run(service.capture_required(ScreenshotPage(), tmp_path / "job"))

    assert SCREENSHOT_SECTIONS is ScreenshotService.SECTIONS
    assert [path.name for path in paths] == [
        section["filename"] for section in SCREENSHOT_SECTIONS
    ]
    assert all(path.read_bytes() == b"full-page-png" for path in paths)
    assert len(warnings) == 2
    assert len(list((tmp_path / "job" / "screenshots").glob("*-fallback.json"))) == 2
    assert not list((tmp_path / "job" / "screenshots").glob("*-fallback.html"))


def test_phase3_cli_parser_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["--generate-clinical-factors", "j1"]).generate_clinical_factors == "j1"
    assert parser.parse_args(["--analyze-cdha", "j2"]).analyze_cdha == "j2"
    assert parser.parse_args(["--process-cdha", "j3"]).process_cdha == "j3"
    assert parser.parse_args(["--login-setup"]).login_setup is True


def make_downloaded_job(repository: JobRepository) -> str:
    job = repository.create_job("https://facebook.com/reel/1")
    repository.transition(job.job_id, WorkflowStatus.DOWNLOADREEL_RUNNING)
    repository.transition(job.job_id, WorkflowStatus.DOWNLOADED)
    return job.job_id


def test_phase3_status_transitions_are_append_only(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    repository.initialize()
    job_id = make_downloaded_job(repository)
    for status in (
        WorkflowStatus.GEMINI_OPENING,
        WorkflowStatus.GEMINI_GENERATING,
        WorkflowStatus.CLINICAL_FACTORS_GENERATED,
        WorkflowStatus.CDHA_OPENING,
        WorkflowStatus.CDHA_UPLOADING,
        WorkflowStatus.CDHA_ANALYZING,
        WorkflowStatus.CDHA_ANALYZED,
        WorkflowStatus.SCREENSHOTS_CAPTURING,
        WorkflowStatus.SCREENSHOTS_CAPTURED,
        WorkflowStatus.WAITING_FOR_REVIEW,
    ):
        repository.transition(job_id, status)

    events = repository.list_events(job_id)
    assert [event.event_id for event in events] == sorted(event.event_id for event in events)
    assert events[-1].to_status is WorkflowStatus.WAITING_FOR_REVIEW


def test_cdha_retry_transition_returns_to_opening(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    repository.initialize()
    job_id = make_downloaded_job(repository)
    for status in (
        WorkflowStatus.GEMINI_OPENING,
        WorkflowStatus.GEMINI_GENERATING,
        WorkflowStatus.CLINICAL_FACTORS_GENERATED,
        WorkflowStatus.CDHA_OPENING,
        WorkflowStatus.CDHA_FAILED,
        WorkflowStatus.RETRY_PENDING,
        WorkflowStatus.CDHA_OPENING,
    ):
        repository.transition(job_id, status)

    assert repository.get_job(job_id).status is WorkflowStatus.CDHA_OPENING
