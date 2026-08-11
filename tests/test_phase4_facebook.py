from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from app.adapters.facebook_adapter import FacebookPublisherAdapter
from app.browser.facebook_client import FacebookManualActionRequired, FacebookWebClient
from app.browser.facebook_page_state import FacebookPageState
from app.config.settings import Settings
from app.main import _run_phase4_command, build_parser
from app.models.results import (
    FacebookCommentResult,
    FacebookPermalinkResult,
    FacebookPostPreparationResult,
    FacebookPublishResult,
    FacebookWorkflowResult,
)
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.post_content_service import (
    FACEBOOK_SCREENSHOT_ORDER,
    PostContentService,
    PostContentValidationError,
)
from app.workflows.state_machine import InvalidTransitionError, WorkflowStateMachine


def make_settings(tmp_path: Path, **changes: Any) -> Settings:
    base = replace(
        Settings.from_env(env_file=tmp_path / "missing.env"),
        database_path=tmp_path / "jobs.sqlite3",
        job_data_dir=tmp_path / "jobs",
        chrome_profile_dir=tmp_path / "profile",
        facebook_target_url="https://www.facebook.com/authorized.page",
    )
    return replace(base, **changes)


def make_png(path: Path, color: str = "red", size: tuple[int, int] = (2, 2)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=color).save(path)
    return path


def approved_job(repository: JobRepository, *, job_id: str = "approved") -> str:
    job = repository.create_job("https://facebook.com/reel/source", job_id=job_id)
    for status in (
        WorkflowStatus.DOWNLOADREEL_RUNNING, WorkflowStatus.DOWNLOADED,
        WorkflowStatus.GEMINI_OPENING, WorkflowStatus.GEMINI_GENERATING,
        WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.CDHA_OPENING,
        WorkflowStatus.CDHA_UPLOADING, WorkflowStatus.CDHA_ANALYZING,
        WorkflowStatus.CDHA_ANALYZED, WorkflowStatus.SCREENSHOTS_CAPTURING,
        WorkflowStatus.SCREENSHOTS_CAPTURED, WorkflowStatus.WAITING_FOR_REVIEW,
        WorkflowStatus.APPROVED,
    ):
        repository.transition(job.job_id, status)
    repository.update_data(job.job_id, {
        "cdha_result": {"key_findings": ["Tổn thương giảm âm"], "impression": "Theo dõi tổn thương gan"},
        "cdha_result_json_path": "/protected/cdha-result.json",
        "cdha_view_url": "https://cdha.ai/dash?view=fixture-result",
    })
    return job.job_id


def make_repo(settings: Settings) -> JobRepository:
    repo = JobRepository(settings.database_path)
    repo.initialize()
    return repo


def valid_post_text(settings: Settings, repository: JobRepository, job_id: str) -> str:
    job = repository.get_job(job_id)
    cdha = job.data["cdha_result"]
    return PostContentService(settings).build_post(
        source_url=job.source_url,
        key_findings=list(cdha["key_findings"]),
        impression=str(cdha["impression"]),
        cdha_view_url=str(job.data["cdha_view_url"]),
    )


def test_facebook_target_validation_and_normalization(tmp_path: Path) -> None:
    service = PostContentService(make_settings(tmp_path))
    assert service.normalize_target_url(
        "https://m.facebook.com/groups/123/?ref=share"
    ) == "https://www.facebook.com/groups/123"
    assert service.normalize_target_url(
        "https://facebook.com/reel/123?tracking=ignored"
    ) == "https://www.facebook.com/reel/123"
    assert service.normalize_target_url(
        "https://facebook.com/page/posts/456?ref=share"
    ) == "https://www.facebook.com/page/posts/456"
    for bad in (
        "",
        "facebook.com/page",
        "http://facebook.com/page",
        "https://example.com/page",
        "https://facebook.example.com/page",
        "https://facebook.com/",
        "https://fb.watch/example",
    ):
        with pytest.raises(PostContentValidationError):
            service.normalize_target_url(bad)


def test_post_template_is_multiline_vietnamese_and_has_disclaimer(tmp_path: Path) -> None:
    service = PostContentService(make_settings(tmp_path))
    text = service.build_post(
        source_url="https://facebook.com/reel/1",
        key_findings=["Tổn thương giảm âm", "Bờ không đều"],
        impression="Cần đối chiếu lâm sàng",
        cdha_view_url="https://cdha.ai/dash?view=result-1",
    )
    assert "📌 CA LÂM SÀNG SIÊU ÂM" in text
    assert "• Tổn thương giảm âm\n• Bờ không đều" in text
    assert "không thay thế việc thăm khám" in text
    assert "https://facebook.com/reel/1" in text
    assert "https://cdha.ai/dash?view=result-1" in text
    assert "&ref=CD2ED52966" not in text


def test_post_rejects_label_only_summary_and_missing_analysis_url(tmp_path: Path) -> None:
    service = PostContentService(make_settings(tmp_path))
    with pytest.raises(PostContentValidationError, match="Key Findings"):
        service.build_post(
            source_url="https://facebook.com/reel/1",
            key_findings=["Key findings:"],
            impression="Impression:",
            cdha_view_url="https://cdha.ai/dash?view=result-1",
        )
    with pytest.raises(PostContentValidationError, match="analysis URL"):
        service.build_post(
            source_url="https://facebook.com/reel/1",
            key_findings=["Ghi nhận tổn thương 19.60 mm."],
            impression="Hình ảnh gợi ý tổn thương.",
        )


def test_post_normalizes_measurement_decimal_without_changing_raw_summary(tmp_path: Path) -> None:
    service = PostContentService(make_settings(tmp_path))
    text = service.build_post(
        source_url="https://facebook.com/reel/1",
        key_findings=["Đường kính ghi nhận 19.60 mm."],
        impression="Hình ảnh gợi ý tổn thương, cần đối chiếu.",
        cdha_view_url="https://cdha.ai/dash?view=result-1",
    )
    assert "19,60 mm" in text
    assert "19.60 mm" not in text


def test_missing_findings_or_impression_requires_manual_edit(tmp_path: Path) -> None:
    service = PostContentService(make_settings(tmp_path))
    with pytest.raises(PostContentValidationError, match="Key Findings"):
        service.build_post(source_url="source", key_findings=[], impression="x")
    with pytest.raises(PostContentValidationError, match="Impression"):
        service.build_post(source_url="source", key_findings=["x"], impression=None)


def test_post_rejects_patient_identifiers_local_paths_and_credentials(tmp_path: Path) -> None:
    service = PostContentService(make_settings(tmp_path))
    for text in (
        "Bệnh nhân: Nguyễn Văn An", "Xem /home/user/private.png",
        "authorization=Bearer secret",
    ):
        with pytest.raises(PostContentValidationError):
            service.build_post(
                source_url="https://facebook.com/reel/1", key_findings=[],
                impression=None, operator_text=text,
            )


def test_screenshot_order_missing_warning_and_integrity(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    service = PostContentService(settings)
    folder = settings.job_data_dir / "j" / "screenshots"
    make_png(folder / FACEBOOK_SCREENSHOT_ORDER[0], "red")
    images, warnings = service.select_screenshots("j")
    assert FACEBOOK_SCREENSHOT_ORDER == (
        "01-detailed-analysis.png",
        "02-final-result.png",
    )
    assert [path.name for path in images] == [FACEBOOK_SCREENSHOT_ORDER[0]]
    assert len(warnings) == 1


def test_zero_byte_unsupported_corrupt_size_and_count_rejections(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    service = PostContentService(settings)
    empty = tmp_path / "empty.png"; empty.write_bytes(b"")
    with pytest.raises(PostContentValidationError, match="empty"):
        service.validate_image(empty)
    bad = tmp_path / "bad.gif"; bad.write_bytes(b"GIF89a")
    with pytest.raises(PostContentValidationError, match="Unsupported"):
        service.validate_image(bad)
    corrupt = tmp_path / "bad.png"; corrupt.write_bytes(b"not png")
    with pytest.raises(PostContentValidationError, match="cannot be opened"):
        service.validate_image(corrupt)
    huge_settings = make_settings(tmp_path, facebook_max_image_size_mb=1)
    huge = tmp_path / "huge.png"; huge.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(PostContentValidationError, match="exceeds"):
        PostContentService(huge_settings).validate_image(huge)
    count_settings = make_settings(tmp_path, facebook_max_image_count=1)
    folder = count_settings.job_data_dir / "many" / "screenshots"
    make_png(folder / FACEBOOK_SCREENSHOT_ORDER[0])
    make_png(folder / FACEBOOK_SCREENSHOT_ORDER[1])
    with pytest.raises(PostContentValidationError, match="count"):
        PostContentService(count_settings).select_screenshots("many")


def test_content_hash_is_deterministic_ordered_and_image_sensitive(tmp_path: Path) -> None:
    service = PostContentService(make_settings(tmp_path))
    one = make_png(tmp_path / "one.png", "red")
    two = make_png(tmp_path / "two.png", "blue")
    first = service.content_fingerprint("https://facebook.com/page", "Xin chào", [one, two], "job-1", "url", "cdha")
    assert first == service.content_fingerprint("https://www.facebook.com/page/", "Xin chào", [one, two], "job-1", "url", "cdha")
    assert first != service.content_fingerprint("https://facebook.com/page", "Xin chào", [two, one], "job-1", "url", "cdha")
    make_png(one, "green")
    assert first != service.content_fingerprint("https://facebook.com/page", "Xin chào", [one, two], "job-1", "url", "cdha")


def test_duplicate_verified_and_uncertain_posts_are_blocked_unless_forced(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    target = PostContentService(settings).normalize_target_url(settings.facebook_target_url)
    
    # Create an old verified job
    old = repo.create_job("https://facebook.com/reel/old", job_id="old")
    repo.update_data(old.job_id, {
        "facebook_content_hash": "hash", "facebook_target_url": target,
        "facebook_publication_verified": True,
    })
    
    # Create a new job that will have the same fingerprint
    new_job = approved_job(repo, job_id="new_job")
    # We mock the fingerprint service to return "hash" for this job
    
    from app.adapters.facebook_adapter import FacebookPublisherAdapter
    content = PostContentService(settings)
    # Monkeypatch content_fingerprint and select_screenshots
    content.content_fingerprint = lambda *args, **kwargs: "hash"
    content.select_screenshots = lambda *args, **kwargs: (["mock"], [])
    adapter = FacebookPublisherAdapter(settings, repo, FacebookWebClient(settings, repo, object()), content=content)
    
    validation = adapter.validate_job("new_job")
    assert not validation.valid
    assert any("Duplicate" in e for e in validation.errors)

    # Test uncertain duplicate
    repo.update_data(old.job_id, {
        "facebook_publication_verified": False,
        "facebook_publication_uncertain": True,
    })
    
    validation2 = adapter.validate_job("new_job")
    assert not validation2.valid
    assert any("Duplicate" in e for e in validation2.errors)


def test_selector_configuration_has_exact_publish_and_no_broad_button_fallback(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    from app.browser.selector_resolver import SelectorResolver
    resolver = SelectorResolver(settings.selectors_path)
    next_button = resolver.candidates("facebook.next_button")
    post_button = resolver.candidates("facebook.post_button")
    publish_now = resolver.candidates("facebook.publish_now_indicator")
    assert any(item.get("name") == "Tiếp" and item.get("exact") for item in next_button if isinstance(item, dict))
    assert any(item.get("name") == "Đăng" and item.get("exact") for item in post_button if isinstance(item, dict))
    assert not any(item.get("name") in {"Đăng", "Post"} for item in next_button if isinstance(item, dict))
    assert any(item.get("text") == "Đăng ngay" and item.get("exact") for item in publish_now if isinstance(item, dict))
    assert not any(item.get("role") == "button" for item in publish_now if isinstance(item, dict))
    rendered = json.dumps(next_button, ensure_ascii=False).casefold()
    rendered_now = json.dumps(publish_now, ensure_ascii=False).casefold()
    assert "first enabled" not in rendered
    assert "not(@aria-disabled" not in rendered
    assert ":has-text" not in rendered
    assert "lên lịch đăng sau" not in rendered
    assert "schedule for later" not in rendered
    assert "lên lịch đăng sau" not in rendered_now
    assert "schedule for later" not in rendered_now
    for forbidden_key in (
        "facebook.publish_now_button",
        "facebook.schedule_dialog",
        "facebook.schedule_heading",
        "facebook.schedule_back_button",
    ):
        with pytest.raises(KeyError):
            resolver.candidates(forbidden_key)


def test_publication_requires_strong_plus_supporting_signal() -> None:
    assert not FacebookWebClient.publication_is_verified({"composer_closed": True})
    assert not FacebookWebClient.publication_is_verified({"success_notification": True})
    assert FacebookWebClient.publication_is_verified({
        "success_notification": True, "composer_closed": True
    })
    assert FacebookWebClient.publication_is_verified({
        "exact_post_match": True, "text_match": True
    })


@pytest.mark.parametrize("raw,expected", [
    ("/page/posts/123?__cft__=x", "https://www.facebook.com/page/posts/123"),
    ("https://facebook.com/permalink/456/?ref=share", "https://www.facebook.com/permalink/456"),
    ("https://m.facebook.com/groups/1/posts/789/?mibextid=x", "https://www.facebook.com/groups/1/posts/789"),
    ("https://facebook.com/story.php?story_fbid=22&id=11&ref=x", "https://www.facebook.com/story.php?story_fbid=22&id=11"),
    ("https://facebook.com/photo.php?fbid=44&set=x", "https://www.facebook.com/photo.php?fbid=44"),
])
def test_exact_permalink_normalization_forms(raw: str, expected: str) -> None:
    assert FacebookWebClient.normalize_permalink(
        raw, base_url="https://www.facebook.com/page"
    ) == expected


def test_page_homepage_and_external_url_are_rejected_as_permalinks() -> None:
    for url in ("https://facebook.com/page", "https://example.com/posts/1"):
        with pytest.raises(ValueError):
            FacebookWebClient.normalize_permalink(url, base_url="https://facebook.com/page")


def test_post_id_extraction_and_duplicate_comment_normalization() -> None:
    assert FacebookWebClient.extract_post_id("https://www.facebook.com/groups/1/posts/789") == "789"
    assert FacebookWebClient.extract_post_id("https://www.facebook.com/story.php?story_fbid=22&id=11") == "22"
    assert FacebookWebClient._same_comment("📋 Copy link chia sẻ:\n https://x", "📋  Copy link chia sẻ: https://x")


def test_facebook_result_models_serialize() -> None:
    now = datetime(2026, 7, 23, tzinfo=UTC)
    values = [
        FacebookPostPreparationResult(True, "j", image_paths=["a.png"]),
        FacebookPublishResult(True, "j", published_at=now),
        FacebookPermalinkResult(True, "j", post_url="https://facebook.com/posts/1"),
        FacebookCommentResult(True, "j", posted_at=now, reused=True),
        FacebookWorkflowResult(True, "j", "COMPLETED"),
    ]
    payloads = [value.to_dict() for value in values]
    assert payloads[1]["published_at"] == now.isoformat()
    assert payloads[3]["reused"] is True


def test_valid_and_invalid_facebook_transitions() -> None:
    success = [
        (WorkflowStatus.APPROVED, WorkflowStatus.FACEBOOK_PREPARING),
        (WorkflowStatus.FACEBOOK_PREPARING, WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW),
        (WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW, WorkflowStatus.FACEBOOK_PUBLISHING),
        (WorkflowStatus.FACEBOOK_PUBLISHING, WorkflowStatus.FACEBOOK_PUBLISHED),
        (WorkflowStatus.FACEBOOK_PUBLISHED, WorkflowStatus.POST_URL_EXTRACTING),
        (WorkflowStatus.POST_URL_EXTRACTING, WorkflowStatus.POST_URL_EXTRACTED),
        (WorkflowStatus.POST_URL_EXTRACTED, WorkflowStatus.COMMENT_ADDING),
        (WorkflowStatus.COMMENT_ADDING, WorkflowStatus.COMMENT_ADDED),
        (WorkflowStatus.COMMENT_ADDED, WorkflowStatus.COMPLETED),
    ]
    for current, target in success:
        WorkflowStateMachine.validate(current, target)
    with pytest.raises(InvalidTransitionError):
        WorkflowStateMachine.validate(WorkflowStatus.APPROVED, WorkflowStatus.FACEBOOK_PUBLISHING)
    assert WorkflowStatus.FACEBOOK_PREPARING not in WorkflowStateMachine.allowed_targets(
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
    )


class FakeLocator:
    def __init__(self) -> None:
        self.value = ""; self.files: list[str] = []; self.clicked = 0
    async def click(self) -> None: self.clicked += 1
    async def fill(self, value: str) -> None: self.value = value
    async def input_value(self) -> str: return self.value
    async def set_input_files(self, files: list[str]) -> None: self.files = list(files)
    async def is_enabled(self) -> bool: return True


class FakeResolver:
    def __init__(self) -> None:
        self.scopes: dict[str, list[Any]] = {}
        self.items = {key: FakeLocator() for key in (
            "facebook.create_post_entry", "facebook.composer_dialog",
            "facebook.composer_textbox", "facebook.file_input",
            "facebook.next_button", "facebook.publish_now_indicator",
            "facebook.post_button",
        )}
    async def find_first(self, page: Any, key: str, **_: Any) -> FakeLocator:
        self.scopes.setdefault(key, []).append(page)
        return self.items[key]
    async def exists(self, page: Any, key: str, **_: Any) -> bool:
        if key in {"facebook.login_indicators", "facebook.checkpoint_indicators", "facebook.target_access_denied", "facebook.upload_error"}:
            return False
        return key == "facebook.authenticated_marker"


class FakePage:
    url = "https://www.facebook.com/authorized.page"
    def __init__(self) -> None: self.shots: list[str] = []
    async def goto(self, *_: Any, **__: Any) -> None: return None
    async def screenshot(self, *, path: str, **_: Any) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True); Path(path).write_bytes(b"png"); self.shots.append(path)
    async def content(self) -> str: return "<html>fixture</html>"
    async def title(self) -> str: return "Facebook fixture"
    def is_closed(self) -> bool: return False


class FakeChrome:
    def __init__(self) -> None: self.page = FakePage()
    async def new_page(self) -> FakePage: return self.page


class PreparedClient(FacebookWebClient):
    async def _wait_for_uploads(self, page: Any, expected: int) -> int: return expected


def test_composer_inserts_exact_multiline_text_and_uploads_images_in_order(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings); job_id = approved_job(repo)
    resolver = FakeResolver(); client = PreparedClient(settings, repo, FakeChrome(), resolver=resolver)
    images = [make_png(tmp_path / "b.png", "blue"), make_png(tmp_path / "a.png", "red")]
    text = "Dòng một\nDòng hai tiếng Việt"
    result = asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url, post_text=text, image_paths=images, job_id=job_id
    ))
    assert result.success and result.uploaded_image_count == 2
    assert resolver.items["facebook.composer_textbox"].value == text
    assert resolver.items["facebook.file_input"].files == [str(path.resolve()) for path in images]
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW


class PublishClient(PreparedClient):
    async def _visible_post_ids(self, page: Any) -> set[str]: return {"old"}
    async def _verify_publication(self, page: Any, job_id: str, text: str, images: list[Path], started: datetime, before_ids: set[str]):
        result = FacebookPublishResult(
            True, job_id, self.settings.facebook_target_url, "new", "https://www.facebook.com/posts/new",
            datetime.now(UTC), "exact_post_match+text_match"
        )
        return result, {"exact_post_match": True, "text_match": True}


def test_final_manual_gate_cancel_does_not_click_and_publish_approval_does(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings); job_id = approved_job(repo)
    resolver = FakeResolver(); client = PublishClient(settings, repo, FakeChrome(), resolver=resolver, confirmation_provider=lambda _: "2")
    image = make_png(tmp_path / "one.png")
    asyncio.run(client.prepare_post(target_url=settings.facebook_target_url, post_text=valid_post_text(settings, repo, job_id), image_paths=[image], job_id=job_id))
    cancelled = asyncio.run(client.publish_prepared_post(job_id=job_id))
    assert not cancelled.success and resolver.items["facebook.next_button"].clicked == 0
    assert repo.get_job(job_id).status is WorkflowStatus.APPROVED

    job2 = approved_job(repo, job_id="approved2")
    resolver2 = FakeResolver(); client2 = PublishClient(settings, repo, FakeChrome(), resolver=resolver2, confirmation_provider=lambda _: "1")
    asyncio.run(client2.prepare_post(target_url=settings.facebook_target_url, post_text=valid_post_text(settings, repo, job2), image_paths=[image], job_id=job2))
    published = asyncio.run(client2.publish_prepared_post(job_id=job2))
    if not published.success:
        print("PUBLISHED ERROR:", published.error)
    assert published.success and resolver2.items["facebook.next_button"].clicked == 1
    assert repo.get_job(job2).status is WorkflowStatus.FACEBOOK_PUBLISHED


def test_disabled_final_confirmation_publishes_without_prompt(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, facebook_final_confirmation=False)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="automatic-publish")
    resolver = FakeResolver()

    def unexpected_prompt(_prompt: str) -> str:
        raise AssertionError("automatic publishing must not prompt")

    client = PublishClient(
        settings,
        repo,
        FakeChrome(),
        resolver=resolver,
        confirmation_provider=unexpected_prompt,
    )
    image = make_png(tmp_path / "automatic.png")
    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url,
        post_text=valid_post_text(settings, repo, job_id),
        image_paths=[image],
        job_id=job_id,
    ))

    published = asyncio.run(client.publish_prepared_post(job_id=job_id))

    assert published.success is True
    assert resolver.items["facebook.next_button"].clicked == 1
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_PUBLISHED


def test_page_publish_flow_confirms_publish_now_without_opening_schedule(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, facebook_final_confirmation=False)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="publish-now-not-schedule")
    resolver = FakeResolver()
    client = PublishClient(settings, repo, FakeChrome(), resolver=resolver)
    image = make_png(tmp_path / "publish-now.png")

    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url,
        post_text=valid_post_text(settings, repo, job_id),
        image_paths=[image],
        job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))

    assert result.success is True
    assert resolver.items["facebook.next_button"].clicked == 1
    assert resolver.items["facebook.publish_now_indicator"].clicked == 0
    assert resolver.items["facebook.post_button"].clicked == 1
    composer = resolver.items["facebook.composer_dialog"]
    assert resolver.scopes["facebook.next_button"][-1] is composer
    assert resolver.scopes["facebook.publish_now_indicator"][-1] is composer
    assert resolver.scopes["facebook.post_button"][-1] is composer


def test_page_publish_flow_never_clicks_schedule_controls(tmp_path: Path) -> None:
    class PersistentScheduleDialog(FakeLocator):
        async def wait_for(self, **_: Any) -> None:
            raise TimeoutError("Facebook reuses the same dialog node after going back")

    class RememberedScheduleResolver(FakeResolver):
        def __init__(self) -> None:
            super().__init__()
            self.items["facebook.schedule_heading"] = FakeLocator()
            self.items["facebook.schedule_dialog"] = PersistentScheduleDialog()
            self.items["facebook.schedule_back_button"] = FakeLocator()

    settings = make_settings(tmp_path, facebook_final_confirmation=False)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="remembered-schedule")
    resolver = RememberedScheduleResolver()
    client = PublishClient(settings, repo, FakeChrome(), resolver=resolver)
    image = make_png(tmp_path / "remembered-schedule.png")

    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url,
        post_text=valid_post_text(settings, repo, job_id),
        image_paths=[image],
        job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))

    assert result.success is True
    assert resolver.items["facebook.schedule_back_button"].clicked == 0
    assert resolver.items["facebook.publish_now_indicator"].clicked == 0
    assert "facebook.schedule_heading" not in resolver.scopes
    assert "facebook.schedule_back_button" not in resolver.scopes


def test_page_publish_flow_aborts_before_post_when_publish_now_is_not_visible(tmp_path: Path) -> None:
    class MissingPublishNowResolver(FakeResolver):
        async def find_first(self, page: Any, key: str, **kwargs: Any) -> FakeLocator:
            if key == "facebook.publish_now_indicator":
                self.scopes.setdefault(key, []).append(page)
                raise KeyError(key)
            return await super().find_first(page, key, **kwargs)

    settings = make_settings(tmp_path, facebook_final_confirmation=False)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="publish-now-not-visible")
    resolver = MissingPublishNowResolver()
    client = PublishClient(settings, repo, FakeChrome(), resolver=resolver)
    image = make_png(tmp_path / "publish-now-not-visible.png")

    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url,
        post_text=valid_post_text(settings, repo, job_id),
        image_paths=[image],
        job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))

    assert result.success is False
    assert result.status == "PUBLISH_ACTION_FAILED"
    assert resolver.items["facebook.post_button"].clicked == 0
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_PUBLISH_FAILED


def test_page_publish_flow_dismisses_post_publish_upsell_before_verification(tmp_path: Path) -> None:
    class PostPublishUpsellResolver(FakeResolver):
        def __init__(self) -> None:
            super().__init__()
            self.items["facebook.post_publish_upsell_dialog"] = FakeLocator()
            self.items["facebook.post_publish_upsell_dismiss"] = FakeLocator()

        async def exists(self, page: Any, key: str, **kwargs: Any) -> bool:
            if key == "facebook.post_publish_upsell_dialog":
                return True
            return await super().exists(page, key, **kwargs)

    settings = make_settings(tmp_path, facebook_final_confirmation=False)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="post-publish-upsell")
    resolver = PostPublishUpsellResolver()
    client = PublishClient(settings, repo, FakeChrome(), resolver=resolver)
    image = make_png(tmp_path / "post-publish-upsell.png")

    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url,
        post_text=valid_post_text(settings, repo, job_id),
        image_paths=[image],
        job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))

    assert result.success is True
    assert resolver.items["facebook.post_publish_upsell_dismiss"].clicked == 1
    upsell = resolver.items["facebook.post_publish_upsell_dialog"]
    assert resolver.scopes["facebook.post_publish_upsell_dismiss"][-1] is upsell


def test_final_publish_guard_rejects_persisted_label_only_sections(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="invalid-clinical-summary")
    resolver = FakeResolver()
    client = PublishClient(
        settings,
        repo,
        FakeChrome(),
        resolver=resolver,
        confirmation_provider=lambda _: "1",
    )
    image = make_png(tmp_path / "guard.png")
    valid_text = PostContentService(settings).build_post(
        source_url=repo.get_job(job_id).source_url,
        key_findings=["Tổn thương giảm âm"],
        impression="Hình ảnh gợi ý tổn thương, cần đối chiếu.",
        cdha_view_url="https://cdha.ai/dash?view=fixture-result",
    )
    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url,
        post_text=valid_text,
        image_paths=[image],
        job_id=job_id,
    ))
    repo.update_data(job_id, {
        "facebook_post_text": "🔍 Ghi nhận chính:\n• Key findings:\n\n📝 Nhận định:\nImpression:"
    })

    with pytest.raises(PostContentValidationError):
        asyncio.run(client.publish_prepared_post(job_id=job_id))

    assert resolver.items["facebook.next_button"].clicked == 0
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW


def test_crash_after_publish_reconciles_without_second_click(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="publish-crash")
    image = make_png(tmp_path / "reconcile.png")
    repo.transition(job_id, WorkflowStatus.FACEBOOK_PREPARING, data_patch={
        "facebook_target_url": settings.facebook_target_url,
        "facebook_post_text": "Nội dung đã đăng",
        "facebook_image_paths": [str(image)],
        "facebook_known_post_ids": ["old"],
        "facebook_publication_started_at": datetime.now(UTC).isoformat(),
    })
    repo.transition(job_id, WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW)
    repo.transition(job_id, WorkflowStatus.FACEBOOK_PUBLISHING)
    resolver = FakeResolver()
    client = PublishClient(settings, repo, FakeChrome(), resolver=resolver)

    result = asyncio.run(client.reconcile_interrupted_publication(job_id=job_id))

    assert result.success
    assert resolver.items["facebook.next_button"].clicked == 0
    persisted = repo.get_job(job_id)
    assert persisted.status is WorkflowStatus.FACEBOOK_PUBLISHED
    assert persisted.data["facebook_post_url"].endswith("/posts/new")


def test_diagnostics_create_deterministic_metadata_and_screenshot(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    job_id = approved_job(repo, job_id="j")
    client = FacebookWebClient(settings, repo, FakeChrome())
    screenshot, metadata = asyncio.run(client._save_diagnostics(FakePage(), job_id, "comment-failure"))
    assert screenshot.name == "comment-failure.png" and screenshot.is_file()
    assert metadata.name == "comment-failure.json" and metadata.is_file()
    assert not metadata.with_suffix(".html").exists()


def test_phase4_cli_parser_commands_and_force_flag() -> None:
    parser = build_parser()
    cases = {
        "prepare_facebook_post": ["--prepare-facebook-post", "j"],
        "publish_facebook": ["--publish-facebook", "j"],
        "extract_facebook_link": ["--extract-facebook-link", "j"],
        "comment_facebook_link": ["--comment-facebook-link", "j"],
        "complete_facebook": ["--complete-facebook", "j"],
    }
    for attr, args in cases.items(): assert getattr(parser.parse_args(args), attr) == "j"
    assert parser.parse_args(["--facebook-login-setup"]).facebook_login_setup
    args = parser.parse_args(["--prepare-facebook-post", "j", "--force-facebook-publish"])
    assert args.force_facebook_publish


def test_missing_target_is_rejected_before_browser_use(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, facebook_target_url="")
    repo = make_repo(settings); job_id = approved_job(repo)
    adapter = FacebookPublisherAdapter(settings, repo, object())
    result = asyncio.run(adapter.prepare(job_id=job_id))
    assert not result.success
    assert "FACEBOOK_TARGET_URL" in result.error


class LoginResolver(FakeResolver):
    def __init__(self, *, logged_in: bool, denied: bool = False) -> None:
        super().__init__(); self.logged_in = logged_in; self.denied = denied
    async def exists(self, page: Any, key: str, **_: Any) -> bool:
        if key == "facebook.login_indicators": return not self.logged_in
        if key == "facebook.checkpoint_indicators": return False
        if key == "facebook.authenticated_marker": return self.logged_in
        if key == "facebook.target_access_denied": return self.denied
        return False


def test_login_and_access_denied_detection(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    page = FakePage()
    logged_out = FacebookWebClient(settings, repo, FakeChrome(), resolver=LoginResolver(logged_in=False))
    assert not asyncio.run(logged_out.is_authenticated(page))
    denied = PreparedClient(settings, repo, FakeChrome(), resolver=LoginResolver(logged_in=True, denied=True))
    job_id = approved_job(repo); image = make_png(tmp_path / "denied.png")
    result = asyncio.run(denied.prepare_post(
        target_url=settings.facebook_target_url, post_text="Nội dung an toàn",
        image_paths=[image], job_id=job_id,
    ))
    assert not result.success
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_PUBLISH_FAILED


class CheckpointDetector:
    async def detect(self, page: Any):
        return SimpleNamespace(
            state=FacebookPageState.CHECKPOINT, probes=(), url=str(page.url),
            title="Security Check", frames=(), has_dialog=False, elapsed_ms=1,
        )


def test_checkpoint_is_persisted_as_waiting_for_auth_review(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="checkpoint-job")
    repo.transition(job_id, WorkflowStatus.FACEBOOK_PREPARING)
    client = FacebookWebClient(
        settings, repo, FakeChrome(), state_detector=CheckpointDetector()
    )

    with pytest.raises(FacebookManualActionRequired, match="checkpoint"):
        asyncio.run(client._ensure_authenticated(FakePage(), job_id, "test"))

    assert repo.get_job(job_id).status is WorkflowStatus.WAITING_FOR_AUTH_REVIEW
    assert repo.list_events(job_id)[-1].event_type == "FACEBOOK_CHECKPOINT_DETECTED"


class UploadResolver:
    def __init__(self, *, error: bool = False, progress: bool = False) -> None:
        self.error = error; self.progress = progress
    async def exists(self, page: Any, key: str, **_: Any) -> bool:
        if key == "facebook.upload_error": return self.error
        if key == "facebook.upload_progress": return self.progress
        return False


class UploadClient(FacebookWebClient):
    async def _count_all(self, page: Any, key: str) -> int: return 3


def test_upload_progress_preview_count_and_error_handling(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    ready = UploadClient(settings, repo, object(), resolver=UploadResolver())
    assert asyncio.run(ready._wait_for_uploads(object(), 3)) == 3
    failed = UploadClient(settings, repo, object(), resolver=UploadResolver(error=True))
    with pytest.raises(RuntimeError, match="upload error"):
        asyncio.run(failed._wait_for_uploads(object(), 1))


class FakeImageCollection:
    def __init__(self, count: int) -> None: self._count = count
    async def count(self) -> int: return self._count


class FakeArticle:
    def __init__(self, text: str, images: int) -> None: self.text = text; self.images = images
    async def inner_text(self) -> str: return self.text
    def locator(self, selector: str) -> FakeImageCollection: return FakeImageCollection(self.images)


class DiscoveryClient(FacebookWebClient):
    def __init__(self, *args: Any, articles: list[FakeArticle], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs); self.articles = articles
    async def _all_locators(self, page: Any, key: str) -> list[Any]: return self.articles
    async def _article_links(self, article: Any) -> list[str]:
        return ["/authorized.page/posts/new-post"]
    async def _article_is_recent(self, article: Any, started: datetime) -> bool: return True


def test_exact_new_post_identification_uses_content_new_id_time_and_images(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    client = DiscoveryClient(
        settings, repo, object(), articles=[
            FakeArticle("unrelated post", 2), FakeArticle("Approved clinical post", 2)
        ]
    )
    page = SimpleNamespace(url=settings.facebook_target_url)
    found = asyncio.run(client._find_exact_new_post(
        page, "Approved clinical post", datetime.now(UTC), 2, {"old-post"}
    ))
    assert found and found["post_id"] == "new-post"
    assert found["text_match"] and found["recent"] and found["image_count"] == 2


class PageLayoutDiscoveryClient(DiscoveryClient):
    async def _find_page_layout_post(
        self,
        page: Any,
        approved_text: str,
        expected_images: int,
        before_ids: set[str],
    ) -> dict[str, Any] | None:
        return {
            "url": "https://www.facebook.com/61589210652274/posts/122116192977307021",
            "post_id": "122116192977307021",
            "text_match": True,
            "recent": True,
            "image_count": expected_images,
            "method": "content-urls+pcb-post-id+image-count",
        }


def test_page_layout_pcb_candidate_is_used_when_role_articles_are_empty(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    client = PageLayoutDiscoveryClient(settings, repo, object(), articles=[])
    page = SimpleNamespace(url="https://www.facebook.com/profile.php?id=61589210652274")

    found = asyncio.run(client._find_exact_new_post(
        page,
        "Approved clinical post",
        datetime.now(UTC),
        2,
        {"old-post"},
    ))

    assert found and found["post_id"] == "122116192977307021"


def test_pcb_photo_link_becomes_canonical_page_post_permalink(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    client = FacebookWebClient(settings, repo, object())

    permalink = client._pcb_post_permalink(
        "https://www.facebook.com/photo/?fbid=122116192911307021"
        "&set=pcb.122116192977307021",
        base_url="https://www.facebook.com/profile.php?id=61589210652274",
    )

    assert permalink == (
        "https://www.facebook.com/61589210652274/posts/122116192977307021"
    )


class EmptyLocator:
    async def count(self) -> int:
        return 0


class RecordingMouse:
    def __init__(self) -> None:
        self.wheels: list[tuple[int, int]] = []

    async def wheel(self, x: int, y: int) -> None:
        self.wheels.append((x, y))


class LazyPage:
    url = "https://www.facebook.com/profile.php?id=61589210652274"

    def __init__(self) -> None:
        self.mouse = RecordingMouse()
        self.waits: list[int] = []

    def get_by_role(self, *_args: Any, **_kwargs: Any) -> EmptyLocator:
        return EmptyLocator()

    def get_by_text(self, *_args: Any, **_kwargs: Any) -> EmptyLocator:
        return EmptyLocator()

    def locator(self, _selector: str) -> EmptyLocator:
        return EmptyLocator()

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        self.waits.append(timeout_ms)


def test_page_layout_discovery_scrolls_lazy_page_timeline(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    client = FacebookWebClient(settings, repo, object())
    page = LazyPage()

    found = asyncio.run(client._find_page_layout_post(
        page,
        "Nguồn video: https://www.facebook.com/reel/1\n"
        "Nguồn phân tích: https://cdha.ai/dash?view=1",
        2,
        set(),
    ))

    assert found is None
    assert page.mouse.wheels == [(0, 700)]
    assert page.waits == [1_000]


class NonmatchingPcbLocator:
    async def count(self) -> int:
        return 1

    def nth(self, _index: int) -> "NonmatchingPcbLocator":
        return self

    async def get_attribute(self, _name: str) -> str:
        return "https://www.facebook.com/photo/?fbid=1&set=pcb.999"

    async def evaluate(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class LoadedNonmatchingPage(LazyPage):
    def locator(self, selector: str) -> EmptyLocator | NonmatchingPcbLocator:
        if selector == 'a[href*="set=pcb."]':
            return NonmatchingPcbLocator()
        return EmptyLocator()


def test_page_layout_scrolls_when_loaded_posts_do_not_match(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    client = FacebookWebClient(settings, repo, object())
    page = LoadedNonmatchingPage()

    found = asyncio.run(client._find_page_layout_post(
        page,
        "Nguồn video: https://www.facebook.com/reel/1\n"
        "Nguồn phân tích: https://cdha.ai/dash?view=1",
        2,
        set(),
    ))

    assert found is None
    assert page.mouse.wheels == [(0, 700)]


class SpanSeeMoreLocator:
    def __init__(self) -> None:
        self.evaluated = 0

    async def count(self) -> int:
        return 13

    def nth(self, _index: int) -> "SpanSeeMoreLocator":
        return self

    async def is_visible(self) -> bool:
        return True

    async def evaluate(self, _script: str) -> None:
        self.evaluated += 1


class SpanSeeMorePage:
    def __init__(self) -> None:
        self.more = SpanSeeMoreLocator()

    def get_by_role(self, *_args: Any, **_kwargs: Any) -> EmptyLocator:
        return EmptyLocator()

    def get_by_text(self, label: str, **_kwargs: Any) -> SpanSeeMoreLocator | EmptyLocator:
        return self.more if label == "Xem thêm" else EmptyLocator()


def test_page_layout_expands_span_see_more_when_it_is_not_a_button(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    client = FacebookWebClient(settings, repo, object())
    page = SpanSeeMorePage()

    asyncio.run(client._expand_collapsed_posts(page))

    assert page.more.evaluated == 13


def test_page_layout_uses_stable_url_ids_when_facebook_truncates_links(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    client = FacebookWebClient(settings, repo, object())

    tokens = client._page_layout_match_tokens(
        "Nguồn video: https://www.facebook.com/reel/1484932350100572\n"
        "Nguồn phân tích: https://cdha.ai/dash?view=44088"
    )

    assert tokens == [
        "1484932350100572",
        "44088",
    ]


class VerificationCrashClient(PublishClient):
    async def _verify_publication(self, *args: Any, **kwargs: Any):
        raise TimeoutError("verification crashed after publish click")


class UncertainPublishClient(PublishClient):
    async def _verify_publication(self, *args: Any, **kwargs: Any):
        return FacebookPublishResult(False, kwargs.get("job_id", "j"), error="uncertain"), {
            "composer_closed": True, "success_notification": False,
            "exact_post_match": False,
        }


def test_uncertain_publication_blocks_automatic_retry(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings); job_id = approved_job(repo)
    resolver = FakeResolver(); client = UncertainPublishClient(
        settings, repo, FakeChrome(), resolver=resolver, confirmation_provider=lambda _: "1"
    )
    image = make_png(tmp_path / "uncertain.png")
    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url, post_text=valid_post_text(settings, repo, job_id),
        image_paths=[image], job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))
    assert not result.success
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
    assert WorkflowStatus.FACEBOOK_PREPARING not in WorkflowStateMachine.allowed_targets(
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
    )


def test_exception_after_publish_click_is_uncertain_not_failed(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="post-click-crash")
    resolver = FakeResolver()
    client = VerificationCrashClient(
        settings,
        repo,
        FakeChrome(),
        resolver=resolver,
        confirmation_provider=lambda _: "1",
    )
    image = make_png(tmp_path / "post-click-crash.png")
    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url,
        post_text=valid_post_text(settings, repo, job_id),
        image_paths=[image],
        job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))
    assert not result.success
    assert resolver.items["facebook.next_button"].clicked == 1
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN


def advance_to_post_url_extracted(repo: JobRepository, job_id: str) -> None:
    for status in (
        WorkflowStatus.FACEBOOK_PREPARING,
        WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        WorkflowStatus.FACEBOOK_PUBLISHING,
        WorkflowStatus.FACEBOOK_PUBLISHED,
        WorkflowStatus.POST_URL_EXTRACTING,
        WorkflowStatus.POST_URL_EXTRACTED,
    ):
        repo.transition(job_id, status)


def test_duplicate_comment_from_sqlite_is_reused_without_browser(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings); job_id = approved_job(repo)
    advance_to_post_url_extracted(repo, job_id)
    post_url = "https://www.facebook.com/posts/123"
    comment = PostContentService.build_permalink_comment(post_url)
    repo.update_data(job_id, {"facebook_comment_result": {
        "success": True, "comment_text": comment,
    }})
    client = FacebookWebClient(settings, repo, object())
    result = asyncio.run(client.add_permalink_comment(
        post_url=post_url, comment_text=comment, job_id=job_id
    ))
    assert result.success and result.reused
    assert repo.get_job(job_id).status is WorkflowStatus.COMPLETED


class ResumeClient:
    def __init__(self, repo: JobRepository) -> None:
        self.repo = repo
        self.calls: list[str] = []
        self.comment_post_urls: list[str] = []
        self.comment_texts: list[str] = []
    async def extract_new_post_permalink(self, *, job_id: str, publication_started_at: datetime):
        self.calls.append("extract")
        self.repo.transition(job_id, WorkflowStatus.POST_URL_EXTRACTING)
        self.repo.transition(job_id, WorkflowStatus.POST_URL_EXTRACTED, data_patch={
            "facebook_post_url": "https://www.facebook.com/posts/123"
        })
        return FacebookPermalinkResult(True, job_id, "https://www.facebook.com/posts/123", "123")
    async def add_permalink_comment(
        self,
        *,
        post_url: str,
        comment_text: str,
        job_id: str,
        image_path: Path | None = None,
    ):
        self.calls.append("comment")
        self.comment_post_urls.append(post_url)
        self.comment_texts.append(comment_text)
        self.repo.transition(job_id, WorkflowStatus.COMMENT_ADDING)
        self.repo.transition(job_id, WorkflowStatus.COMMENT_ADDED)
        self.repo.transition(job_id, WorkflowStatus.COMPLETED)
        return FacebookCommentResult(True, job_id, post_url, comment_text=comment_text)


def test_adapter_resume_from_published_and_extracted_without_republishing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    first = approved_job(repo, job_id="published")
    for status in (
        WorkflowStatus.FACEBOOK_PREPARING, WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        WorkflowStatus.FACEBOOK_PUBLISHING, WorkflowStatus.FACEBOOK_PUBLISHED,
    ): repo.transition(first, status)
    repo.update_data(first, {"facebook_publication_started_at": datetime.now(UTC).isoformat()})
    client = ResumeClient(repo); adapter = FacebookPublisherAdapter(settings, repo, client)
    result = asyncio.run(adapter.complete(job_id=first))
    assert result.success and client.calls == ["extract", "comment"]

    second = approved_job(repo, job_id="extracted")
    advance_to_post_url_extracted(repo, second)
    repo.update_data(second, {"facebook_post_url": "https://www.facebook.com/posts/123"})
    client2 = ResumeClient(repo); adapter2 = FacebookPublisherAdapter(settings, repo, client2)
    result2 = asyncio.run(adapter2.complete(job_id=second))
    assert result2.success and client2.calls == ["comment"]
    assert client2.comment_post_urls == ["https://facebook.com/reel/source"]
    assert client2.comment_texts == ["Chi tiết: https://www.facebook.com/posts/123"]


def test_comment_failure_retry_path_does_not_include_republishing() -> None:
    assert WorkflowStateMachine.allowed_targets(WorkflowStatus.COMMENT_FAILED) == {
        WorkflowStatus.RETRY_PENDING, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED
    }
    targets = WorkflowStateMachine.allowed_targets(WorkflowStatus.RETRY_PENDING)
    assert WorkflowStatus.COMMENT_ADDING in targets


def test_manual_gate_edit_saves_exact_text_and_never_clicks(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings); job_id = approved_job(repo)
    original_text = valid_post_text(settings, repo, job_id)
    edited_text = original_text + "\n#DaKiemTra"
    resolver = FakeResolver(); client = PublishClient(
        settings, repo, FakeChrome(), resolver=resolver,
        confirmation_provider=lambda _: "3",
        edit_provider=lambda: edited_text,
    )
    image = make_png(tmp_path / "edit.png")
    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url, post_text=original_text,
        image_paths=[image], job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))
    assert not result.success and resolver.items["facebook.next_button"].clicked == 0
    job = repo.get_job(job_id)
    assert job.status is WorkflowStatus.APPROVED
    assert Path(job.data["facebook_post_text_path"]).read_text(encoding="utf-8") == edited_text
    assert job.data["facebook_post_text"] == edited_text
