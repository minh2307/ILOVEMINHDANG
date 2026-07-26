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
from app.browser.facebook_client import FacebookWebClient
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
    })
    return job.job_id


def make_repo(settings: Settings) -> JobRepository:
    repo = JobRepository(settings.database_path)
    repo.initialize()
    return repo


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
    )
    assert "📌 CA LÂM SÀNG SIÊU ÂM" in text
    assert "• Tổn thương giảm âm\n• Bờ không đều" in text
    assert "không thay thế việc thăm khám" in text
    assert "https://facebook.com/reel/1" in text


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
    first = service.content_fingerprint("https://facebook.com/page", "Xin chào", [one, two])
    assert first == service.content_fingerprint("https://www.facebook.com/page/", "Xin chào", [one, two])
    assert first != service.content_fingerprint("https://facebook.com/page", "Xin chào", [two, one])
    make_png(one, "green")
    assert first != service.content_fingerprint("https://facebook.com/page", "Xin chào", [one, two])


def test_duplicate_verified_and_uncertain_posts_are_blocked_unless_forced(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    target = PostContentService(settings).normalize_target_url(settings.facebook_target_url)
    old = repo.create_job("https://facebook.com/reel/old", job_id="old")
    repo.update_data(old.job_id, {
        "facebook_content_hash": "hash", "facebook_target_url": target,
        "facebook_publication_verified": True,
    })
    client = FacebookWebClient(settings, repo, object())
    with pytest.raises(ValueError, match="Duplicate"):
        client._guard_duplicate("new", target, "hash")
    forced = FacebookWebClient(settings, repo, object(), force_publish=True)
    forced._guard_duplicate("new", target, "hash")
    uncertain = repo.create_job("https://facebook.com/reel/u", job_id="uncertain")
    repo.update_data(uncertain.job_id, {
        "facebook_content_hash": "hash2", "facebook_target_url": target,
        "facebook_publication_uncertain": True,
    })
    with pytest.raises(ValueError, match="uncertain"):
        client._guard_duplicate("new", target, "hash2")


def test_selector_configuration_has_exact_publish_and_no_broad_button_fallback(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    from app.browser.selector_resolver import SelectorResolver
    resolver = SelectorResolver(settings.selectors_path)
    publish = resolver.candidates("facebook.publish_button")
    assert any(item.get("name") == "Đăng" and item.get("exact") for item in publish if isinstance(item, dict))
    rendered = json.dumps(publish, ensure_ascii=False).casefold()
    assert "first enabled" not in rendered
    assert "not(@aria-disabled" not in rendered


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
        self.items = {key: FakeLocator() for key in (
            "facebook.create_post_entry", "facebook.composer_dialog",
            "facebook.composer_textbox", "facebook.file_input", "facebook.publish_button"
        )}
    async def find_first(self, page: Any, key: str, **_: Any) -> FakeLocator:
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
    asyncio.run(client.prepare_post(target_url=settings.facebook_target_url, post_text="Nội dung an toàn", image_paths=[image], job_id=job_id))
    cancelled = asyncio.run(client.publish_prepared_post(job_id=job_id))
    assert not cancelled.success and resolver.items["facebook.publish_button"].clicked == 0
    assert repo.get_job(job_id).status is WorkflowStatus.APPROVED

    job2 = approved_job(repo, job_id="approved2")
    resolver2 = FakeResolver(); client2 = PublishClient(settings, repo, FakeChrome(), resolver=resolver2, confirmation_provider=lambda _: "1")
    asyncio.run(client2.prepare_post(target_url=settings.facebook_target_url, post_text="Nội dung khác", image_paths=[image], job_id=job2))
    published = asyncio.run(client2.publish_prepared_post(job_id=job2))
    assert published.success and resolver2.items["facebook.publish_button"].clicked == 1
    assert repo.get_job(job2).status is WorkflowStatus.FACEBOOK_PUBLISHED


def test_diagnostics_create_deterministic_metadata_and_screenshot(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings)
    client = FacebookWebClient(settings, repo, FakeChrome())
    screenshot, metadata = asyncio.run(client._save_diagnostics(FakePage(), "j", "comment-failure"))
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
    with pytest.raises(ValueError, match="FACEBOOK_TARGET_URL"):
        asyncio.run(adapter.prepare(job_id=job_id))


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
        target_url=settings.facebook_target_url, post_text="Nội dung chưa chắc chắn",
        image_paths=[image], job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))
    assert not result.success
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
    assert WorkflowStatus.FACEBOOK_PREPARING not in WorkflowStateMachine.allowed_targets(
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
    )


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
    def __init__(self, repo: JobRepository) -> None: self.repo = repo; self.calls: list[str] = []
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


def test_comment_failure_retry_path_does_not_include_republishing() -> None:
    assert WorkflowStateMachine.allowed_targets(WorkflowStatus.COMMENT_FAILED) == {
        WorkflowStatus.RETRY_PENDING, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED
    }
    targets = WorkflowStateMachine.allowed_targets(WorkflowStatus.RETRY_PENDING)
    assert WorkflowStatus.COMMENT_ADDING in targets


def test_manual_gate_edit_saves_exact_text_and_never_clicks(tmp_path: Path) -> None:
    settings = make_settings(tmp_path); repo = make_repo(settings); job_id = approved_job(repo)
    resolver = FakeResolver(); client = PublishClient(
        settings, repo, FakeChrome(), resolver=resolver,
        confirmation_provider=lambda _: "3",
        edit_provider=lambda: "Nội dung chỉnh sửa\nGiữ Unicode tiếng Việt",
    )
    image = make_png(tmp_path / "edit.png")
    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url, post_text="Nội dung ban đầu",
        image_paths=[image], job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))
    assert not result.success and resolver.items["facebook.publish_button"].clicked == 0
    job = repo.get_job(job_id)
    assert job.status is WorkflowStatus.APPROVED
    assert Path(job.data["facebook_post_text_path"]).read_text(encoding="utf-8") == job.data["facebook_post_text"]
