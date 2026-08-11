"""Prompt 5 regression tests — Facebook publication integrity, idempotency, and reconciliation.

Covers:
  1. Validation gate: empty caption, placeholders
  2. Content fingerprinting determinism
  3. FacebookPublishResult is_verified property
  4. State machine: uncertain / reconcile transitions
  5. ReconcilePublishUseCase: safe status guard
  6. Post-click submission status persistence
  7. Publication outcome classification (VERIFIED vs UNCERTAIN)
  8. Duplicate detection
  9. Placeholder rejection in validate_post_text

NOTE: Tests in sections 1-5 and 8-9 run on pure Python without playwright.
Tests in sections 6-7 use fakeclasses that mock browser internals.
"""
from __future__ import annotations

import struct
import sys
import types
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Pre-import playwright stub — must happen before importing app.browser modules
# ---------------------------------------------------------------------------
def _stub_playwright() -> None:
    """Inject minimal playwright stubs so app.browser.* can be imported without
    the binary greenlet/_greenlet extension that's compiled for Python 3.12."""
    for name in (
        "playwright",
        "playwright.async_api",
        "playwright.async_api._generated",
        "playwright._impl",
        "playwright._impl._assertions",
        "playwright._impl._connection",
        "playwright._impl._greenlets",
    ):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)

    # playwright.async_api needs Error, Page, Browser, BrowserContext, etc.
    async_api = sys.modules["playwright.async_api"]
    for attr in ("Error", "Page", "Browser", "BrowserContext", "Playwright",
                 "TimeoutError", "async_playwright"):
        if not hasattr(async_api, attr):
            setattr(async_api, attr, type(attr, (Exception if "Error" in attr else object,), {}))

    # greenlet stub
    for name in ("greenlet", "greenlet._greenlet"):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.greenlet = object  # type: ignore[attr-defined]
            sys.modules[name] = mod


_stub_playwright()

# NOW we can safely import app.browser modules
import asyncio  # noqa: E402

from app.application.use_cases.reconcile_publish_use_case import ReconcilePublishUseCase  # noqa: E402
from app.browser.facebook_client import FacebookWebClient  # noqa: E402
from app.config.settings import Settings  # noqa: E402
from app.domain.rules.state_transitions import JobStateTransitions as WorkflowStateMachine  # noqa: E402
from app.models.results import FacebookPublishResult  # noqa: E402
from app.models.workflow import WorkflowStatus  # noqa: E402
from app.repositories.job_repository import JobRepository  # noqa: E402
from app.services.post_content_service import PostContentService, PostContentValidationError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_settings(tmp_path: Path, **changes: Any) -> Settings:
    from dataclasses import replace
    base = replace(
        Settings.from_env(env_file=tmp_path / "missing.env"),
        database_path=tmp_path / "jobs.sqlite3",
        job_data_dir=tmp_path / "jobs",
        chrome_profile_dir=tmp_path / "profile",
        facebook_target_url="https://www.facebook.com/test.page",
    )
    return replace(base, **changes)


def make_repo(settings: Settings) -> JobRepository:
    repo = JobRepository(settings.database_path)
    repo.initialize()
    return repo


def make_png(path: Path, _color: str = "red", size: tuple[int, int] = (2, 2)) -> Path:
    """Write a minimal valid PNG without requiring PIL binary extensions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    raw_row = b"\x00" + bytes([0xFF, 0x00, 0x00] * w)
    raw_data = raw_row * h
    compressed = zlib.compress(raw_data)

    def chunk(tag: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return length + tag + data + crc

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )
    return path


def approved_job(
    repo: JobRepository,
    *,
    job_id: str = "job-test",
    source_url: str = "https://facebook.com/reel/1",
) -> str:
    job = repo.create_job(source_url, job_id=job_id)
    for status in (
        WorkflowStatus.DOWNLOADREEL_RUNNING, WorkflowStatus.DOWNLOADED,
        WorkflowStatus.GEMINI_OPENING, WorkflowStatus.GEMINI_GENERATING,
        WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.CDHA_OPENING,
        WorkflowStatus.CDHA_UPLOADING, WorkflowStatus.CDHA_ANALYZING,
        WorkflowStatus.CDHA_ANALYZED, WorkflowStatus.SCREENSHOTS_CAPTURING,
        WorkflowStatus.SCREENSHOTS_CAPTURED, WorkflowStatus.WAITING_FOR_REVIEW,
        WorkflowStatus.APPROVED,
    ):
        repo.transition(job.job_id, status)
    repo.update_data(job.job_id, {
        "cdha_result": {"key_findings": ["Tổn thương giảm âm"], "impression": "Theo dõi tổn thương"},
        "cdha_view_url": "https://cdha.ai/dash?view=p5-test",
    })
    return job.job_id


# ---------------------------------------------------------------------------
# Section 1: validate_post_text — placeholder gate
# ---------------------------------------------------------------------------

def test_validate_post_text_rejects_empty(tmp_path: Path) -> None:
    svc = PostContentService(make_settings(tmp_path))
    with pytest.raises(PostContentValidationError, match="empty"):
        svc.validate_post_text("")
    with pytest.raises(PostContentValidationError, match="empty"):
        svc.validate_post_text("   ")


def test_validate_post_text_rejects_null_placeholder(tmp_path: Path) -> None:
    svc = PostContentService(make_settings(tmp_path))
    for bad in ("null", "undefined", "n/a", "N/A", "NULL"):
        with pytest.raises(PostContentValidationError, match="(?i)null|n.a|placeholder"):
            svc.validate_post_text(f"Nhận định: {bad}")


def test_validate_post_text_rejects_template_variables(tmp_path: Path) -> None:
    svc = PostContentService(make_settings(tmp_path))
    with pytest.raises(PostContentValidationError, match="template"):
        svc.validate_post_text("Nội dung {{source_url}} cần điền")


def test_validate_post_text_rejects_placeholder_tags(tmp_path: Path) -> None:
    svc = PostContentService(make_settings(tmp_path))
    with pytest.raises(PostContentValidationError, match="(?i)placeholder"):
        svc.validate_post_text("Xem thêm [PLACEHOLDER] tại đây")
    with pytest.raises(PostContentValidationError, match="(?i)placeholder|TODO"):
        svc.validate_post_text("Xem thêm [TODO] tại đây")


def test_validate_post_text_allows_normal_content(tmp_path: Path) -> None:
    """Normal Vietnamese clinical content must NOT raise."""
    svc = PostContentService(make_settings(tmp_path))
    # Must pass source_url so it's excluded from the privacy identifier check
    svc.validate_post_text(
        "📌 CA LÂM SÀNG SIÊU ÂM\n"
        "🔍 Ghi nhận chính:\n• Tổn thương giảm âm\n"
        "📝 Nhận định:\nTheo dõi tổn thương\n"
        "Nguồn video:\nhttps://facebook.com/reel/1",
        source_url="https://facebook.com/reel/1",
    )


# ---------------------------------------------------------------------------
# Section 2: FacebookPublishResult.is_verified property
# ---------------------------------------------------------------------------

def test_publish_result_is_verified_requires_post_id_or_permalink() -> None:
    # success=True but no post evidence → NOT verified
    assert not FacebookPublishResult(success=True, status="OK").is_verified
    # success=True + post_id → verified
    assert FacebookPublishResult(success=True, status="OK", post_id="123").is_verified
    # success=True + permalink → verified
    assert FacebookPublishResult(
        success=True, status="OK", permalink="https://www.facebook.com/posts/1"
    ).is_verified
    # success=False + post_id → NOT verified
    assert not FacebookPublishResult(success=False, status="FAIL", post_id="123").is_verified


def test_publish_result_backward_compatible_positional_construction() -> None:
    """Legacy callers using 2-3 positional args must still work without error."""
    r = FacebookPublishResult(True, "job-123")
    assert r.success is True
    assert r.status == "job-123"
    assert r.target_url == ""
    assert r.post_id is None
    assert r.content_fingerprint == ""
    assert isinstance(r.diagnostics, dict)


def test_publish_result_to_dict_includes_is_verified() -> None:
    r = FacebookPublishResult(
        success=True,
        status="PUBLISHED_VERIFIED",
        target_url="https://www.facebook.com/page",
        post_id="99887766",
        permalink="https://www.facebook.com/posts/99887766",
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
        verification_method="success_notification+composer_closed",
    )
    d = r.to_dict()
    assert d["is_verified"] is True
    assert d["post_id"] == "99887766"
    assert d["verification_method"] == "success_notification+composer_closed"
    assert d["published_at"] is not None


# ---------------------------------------------------------------------------
# Section 3: State machine transitions for uncertainty / reconciliation
# ---------------------------------------------------------------------------

def test_uncertain_can_escalate_to_reconciliation_required() -> None:
    targets = WorkflowStateMachine.allowed_targets(WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN)
    assert WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED in targets


def test_reconciliation_required_resolves_to_published_or_back_to_uncertain() -> None:
    targets = WorkflowStateMachine.allowed_targets(WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED)
    assert WorkflowStatus.FACEBOOK_PUBLISHED in targets
    assert WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN in targets


def test_uncertain_cannot_directly_transition_to_preparing_or_publishing() -> None:
    targets = WorkflowStateMachine.allowed_targets(WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN)
    assert WorkflowStatus.FACEBOOK_PREPARING not in targets
    assert WorkflowStatus.FACEBOOK_PUBLISHING not in targets


def test_reconcile_lifecycle_persists_correctly(tmp_path: Path) -> None:
    """Full uncertain lifecycle must survive round-trips through SQLite."""
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo)
    for st in (
        WorkflowStatus.FACEBOOK_PREPARING,
        WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        WorkflowStatus.FACEBOOK_PUBLISHING,
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
        WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED,
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,  # can loop back
    ):
        repo.transition(job_id, st)
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN


# ---------------------------------------------------------------------------
# Section 4: ReconcilePublishUseCase — safety guard
# ---------------------------------------------------------------------------

class _FakePublisher:
    def __init__(self, *, success: bool = True, post_id: str = "99887766") -> None:
        self._success = success
        self._post_id = post_id
        self.calls: list[str] = []

    async def reconcile_publication(self, *, job_id: str) -> FacebookPublishResult:
        self.calls.append(job_id)
        return FacebookPublishResult(
            success=self._success,
            status="RECONCILE_DONE",
            post_id=self._post_id if self._success else None,
            job_id=job_id,
        )


def test_reconcile_use_case_rejects_non_uncertain_job(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo)  # APPROVED status

    publisher = _FakePublisher()
    use_case = ReconcilePublishUseCase(repo, publisher)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="PUBLISH_RECONCILIATION_REQUIRED|FACEBOOK_PUBLISH_UNCERTAIN"):
        asyncio.run(use_case.execute(job_id))
    assert publisher.calls == []  # publisher never called


def test_reconcile_use_case_accepts_uncertain_status(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo)
    for st in (
        WorkflowStatus.FACEBOOK_PREPARING,
        WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        WorkflowStatus.FACEBOOK_PUBLISHING,
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
    ):
        repo.transition(job_id, st)

    publisher = _FakePublisher(success=True)
    use_case = ReconcilePublishUseCase(repo, publisher)  # type: ignore[arg-type]
    result = asyncio.run(use_case.execute(job_id))
    assert result.success
    assert publisher.calls == [job_id]


def test_reconcile_use_case_enters_formal_reconciliation_state_before_lookup(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo)
    for st in (
        WorkflowStatus.FACEBOOK_PREPARING,
        WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        WorkflowStatus.FACEBOOK_PUBLISHING,
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
    ):
        repo.transition(job_id, st)

    class StatusCheckingPublisher(_FakePublisher):
        async def reconcile_publication(self, *, job_id: str) -> FacebookPublishResult:
            assert repo.get_job(job_id).status is WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED
            return await super().reconcile_publication(job_id=job_id)

    result = asyncio.run(
        ReconcilePublishUseCase(repo, StatusCheckingPublisher()).execute(job_id)
    )

    assert result.success


def test_reconcile_use_case_accepts_reconciliation_required_status(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo)
    for st in (
        WorkflowStatus.FACEBOOK_PREPARING,
        WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        WorkflowStatus.FACEBOOK_PUBLISHING,
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
        WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED,
    ):
        repo.transition(job_id, st)

    publisher = _FakePublisher(success=True)
    use_case = ReconcilePublishUseCase(repo, publisher)  # type: ignore[arg-type]
    result = asyncio.run(use_case.execute(job_id))
    assert result.success


def test_reconcile_use_case_recovers_interrupted_publishing_without_publish_click(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="interrupted-submit")
    for status in (
        WorkflowStatus.FACEBOOK_PREPARING,
        WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        WorkflowStatus.FACEBOOK_PUBLISHING,
    ):
        repo.transition(job_id, status)
    repo.update_data(job_id, {
        "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
        "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
    })
    publisher = _FakePublisher(success=True)

    result = asyncio.run(
        ReconcilePublishUseCase(repo, publisher).execute(job_id)  # type: ignore[arg-type]
    )

    assert result.success is True
    assert publisher.calls == [job_id]
    assert repo.get_job(job_id).status is WorkflowStatus.PUBLISH_RECONCILIATION_REQUIRED


# ---------------------------------------------------------------------------
# Section 5: Content fingerprinting determinism
# ---------------------------------------------------------------------------

def test_fingerprint_stable_across_url_normalizations(tmp_path: Path) -> None:
    svc = PostContentService(make_settings(tmp_path))
    img = make_png(tmp_path / "img.png")
    text = "Nội dung bài đăng"
    h1 = svc.content_fingerprint("https://www.facebook.com/page", text, [img], "job-1", "https://facebook.com/reel/1", "https://cdha.ai/1")
    h2 = svc.content_fingerprint("https://facebook.com/page/", text, [img], "job-1", "https://facebook.com/reel/1", "https://cdha.ai/1")
    assert h1 == h2


def test_fingerprint_changes_with_different_image_contents(tmp_path: Path) -> None:
    svc = PostContentService(make_settings(tmp_path))
    img1 = make_png(tmp_path / "a.png", "red")
    img2 = make_png(tmp_path / "b.png", "blue")
    # Write different bytes to distinguish them
    img2.write_bytes(img2.read_bytes() + b"\x00\x01\x02")
    h1 = svc.content_fingerprint("https://www.facebook.com/page", "text", [img1], "job-1", "https://facebook.com/reel/1", "https://cdha.ai/1")
    h2 = svc.content_fingerprint("https://www.facebook.com/page", "text", [img2], "job-1", "https://facebook.com/reel/1", "https://cdha.ai/1")
    assert h1 != h2


def test_fingerprint_changes_with_different_post_text(tmp_path: Path) -> None:
    svc = PostContentService(make_settings(tmp_path))
    img = make_png(tmp_path / "img.png")
    h1 = svc.content_fingerprint("https://www.facebook.com/page", "text A", [img], "job-1", "https://facebook.com/reel/1", "https://cdha.ai/1")
    h2 = svc.content_fingerprint("https://www.facebook.com/page", "text B", [img], "job-1", "https://facebook.com/reel/1", "https://cdha.ai/1")
    assert h1 != h2


# ---------------------------------------------------------------------------
# Section 6: Submission status persistence — browser interaction fakes
# ---------------------------------------------------------------------------

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
            "facebook.composer_textbox", "facebook.file_input", "facebook.next_button",
            "facebook.publish_now_indicator", "facebook.post_button",
        )}

    async def find_first(self, page: Any, key: str, **_: Any) -> FakeLocator:
        return self.items[key]

    async def exists(self, page: Any, key: str, **_: Any) -> bool:
        if key in {"facebook.login_indicators", "facebook.checkpoint_indicators",
                   "facebook.target_access_denied", "facebook.upload_error"}:
            return False
        return key == "facebook.authenticated_marker"

    def candidates(self, key: str) -> list:
        return []


class FakePage:
    url = "https://www.facebook.com/test.page"

    def __init__(self) -> None: self.shots: list[str] = []

    async def goto(self, *_: Any, **__: Any) -> None: return None
    async def reload(self, *_: Any, **__: Any) -> None: return None
    async def close(self) -> None: return None

    async def screenshot(self, *, path: str, **_: Any) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")
        self.shots.append(path)

    async def content(self) -> str: return "<html>fixture</html>"
    async def title(self) -> str: return "Facebook fixture"
    def is_closed(self) -> bool: return False


class FakeChrome:
    def __init__(self) -> None: self.page = FakePage()
    async def new_page(self) -> FakePage: return self.page


class VerifiedPublishClient(FacebookWebClient):
    """Simulates a successful publish returning verified post_id and permalink."""

    async def _wait_for_uploads(self, page: Any, expected: int) -> int:
        return expected

    async def _visible_post_ids(self, page: Any) -> set[str]:
        return {"old-id"}

    async def _verify_publication(
        self, page: Any, job_id: str, text: str, images: list[Path],
        started: datetime, before_ids: set[str],
    ):
        result = FacebookPublishResult(
            success=True,
            status="PUBLISHED_VERIFIED",
            target_url=self.settings.facebook_target_url,
            post_id="987654321",
            permalink="https://www.facebook.com/posts/987654321",
            published_at=datetime.now(UTC),
            verification_method="exact_post_match+text_match",
            job_id=job_id,
            post_url="https://www.facebook.com/posts/987654321",
        )
        return result, {"exact_post_match": True, "text_match": True, "composer_closed": True}


def _valid_post_text(settings: Settings) -> str:
    return PostContentService(settings).build_post(
        source_url="https://facebook.com/reel/1",
        key_findings=["Tổn thương giảm âm"],
        impression="Theo dõi tổn thương",
        cdha_view_url="https://cdha.ai/dash?view=p5-test",
    )


def test_verified_publish_persists_post_url_and_submission_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """After a verified publish, facebook_post_url and facebook_submission_status=VERIFIED
    must be written to the database."""
    # Bypass PIL binary check (PIL compiled for Py3.12, running on Py3.14)
    monkeypatch.setattr(
        PostContentService, "validate_image",
        lambda self, image_path: Path(image_path).expanduser().resolve(),
    )
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo)
    image = make_png(tmp_path / "img.png")

    resolver = FakeResolver()
    client = VerifiedPublishClient(
        settings, repo, FakeChrome(), resolver=resolver,
        confirmation_provider=lambda _: "1",
    )

    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url,
        post_text=_valid_post_text(settings),
        image_paths=[image],
        job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))

    assert result.success is True
    assert result.is_verified is True
    assert result.post_id == "987654321"

    persisted = repo.get_job(job_id)
    assert persisted.status is WorkflowStatus.FACEBOOK_PUBLISHED
    assert persisted.data.get("facebook_submission_status") == "VERIFIED"
    assert persisted.data.get("facebook_post_url") == "https://www.facebook.com/posts/987654321"
    assert persisted.data.get("facebook_post_id") == "987654321"
    assert persisted.data.get("facebook_verification_method") == "exact_post_match+text_match"


def test_uncertain_publish_persists_submission_status_and_uncertain_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When verification fails after publish click, data must reflect PUBLICATION_UNCERTAIN."""
    monkeypatch.setattr(
        PostContentService, "validate_image",
        lambda self, image_path: Path(image_path).expanduser().resolve(),
    )
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo)
    image = make_png(tmp_path / "img.png")

    class UncertainClient(VerifiedPublishClient):
        async def _verify_publication(self, page: Any, job_id: str, text: str,
                                      images: list[Path], started: datetime, before_ids: set[str]):
            return FacebookPublishResult(
                success=False, status="PUBLICATION_UNCERTAIN", job_id=job_id,
                error="Could not confirm publication",
            ), {"composer_closed": True, "success_notification": False}

    client = UncertainClient(
        settings, repo, FakeChrome(), resolver=FakeResolver(), confirmation_provider=lambda _: "1",
    )
    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url,
        post_text=_valid_post_text(settings),
        image_paths=[image],
        job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))

    assert result.success is False
    assert result.is_verified is False
    persisted = repo.get_job(job_id)
    assert persisted.status is WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
    assert persisted.data.get("facebook_submission_status") == "SUBMITTED_UNCONFIRMED"
    assert persisted.data.get("facebook_publication_state") == "SUBMITTED_UNCONFIRMED"
    assert persisted.data.get("facebook_publication_uncertain") is True


def test_verification_retries_delayed_post_publish_upsell(tmp_path: Path) -> None:
    """A late Facebook CTA must be dismissed during verification, not only once after click."""
    settings = make_settings(tmp_path, facebook_publish_timeout_seconds=5)
    repo = make_repo(settings)
    job_id = approved_job(repo)
    repo.update_data(job_id, {"facebook_target_url": settings.facebook_target_url})

    dialog = FakeLocator()
    dismiss = FakeLocator()

    class DelayedUpsellResolver(FakeResolver):
        def __init__(self) -> None:
            super().__init__()
            self.upsell_probes = 0

        async def exists(self, page: Any, key: str, **_: Any) -> bool:
            if key == "facebook.post_publish_upsell_dialog":
                self.upsell_probes += 1
                return self.upsell_probes == 2
            if key in {"facebook.publish_success", "facebook.composer_dialog"}:
                return False
            return await super().exists(page, key)

        async def find_first(self, page: Any, key: str, **_: Any) -> FakeLocator:
            if key == "facebook.post_publish_upsell_dialog":
                return dialog
            if key == "facebook.post_publish_upsell_dismiss":
                return dismiss
            return await super().find_first(page, key)

    class DelayedCandidateClient(FacebookWebClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.candidate_checks = 0

        async def _find_exact_new_post(self, *_: Any, **__: Any) -> dict[str, Any] | None:
            self.candidate_checks += 1
            if self.candidate_checks < 2:
                return None
            return {
                "url": "https://www.facebook.com/posts/123456789",
                "text_match": True,
                "recent": True,
                "image_count": 1,
            }

    resolver = DelayedUpsellResolver()
    client = DelayedCandidateClient(settings, repo, FakeChrome(), resolver=resolver)
    result, _signals = asyncio.run(client._verify_publication(
        FakePage(), job_id, "Nội dung", [tmp_path / "img.png"], datetime.now(UTC), set()
    ))

    assert result.success is True
    assert resolver.upsell_probes >= 2
    assert dismiss.clicked == 1


def test_publish_settle_observes_posting_and_dismisses_late_interstitial(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        facebook_publish_settle_timeout_seconds=2,
        facebook_publish_poll_interval_seconds=0.001,
    )
    repo = make_repo(settings)
    job_id = approved_job(repo)
    dialog = FakeLocator()
    dismiss = FakeLocator()

    class SettleResolver(FakeResolver):
        def __init__(self) -> None:
            super().__init__()
            self.posting_probes = 0
            self.dialog_probes = 0

        async def exists(self, page: Any, key: str, **_: Any) -> bool:
            if key == "facebook.post_publish_interstitial_dialog":
                self.dialog_probes += 1
                return self.dialog_probes >= 2 and dismiss.clicked == 0
            if key == "facebook.posting_indicator":
                self.posting_probes += 1
                return self.posting_probes <= 2
            if key == "facebook.composer_dialog":
                return self.posting_probes <= 2
            if key == "facebook.publish_success":
                return False
            return await super().exists(page, key)

        async def find_first(self, page: Any, key: str, **_: Any) -> FakeLocator:
            if key == "facebook.post_publish_interstitial_dialog":
                return dialog
            if key == "facebook.post_publish_interstitial_dismiss":
                return dismiss
            return await super().find_first(page, key)

    resolver = SettleResolver()
    client = FacebookWebClient(settings, repo, FakeChrome(), resolver=resolver)
    settled = asyncio.run(client._wait_for_publish_to_settle(
        FakePage(), job_id=job_id, submitted_at=datetime.now(UTC)
    ))

    assert settled.submitted is True
    assert settled.posting_indicator_detected is True
    assert settled.posting_indicator_cleared is True
    assert settled.composer_closed is True
    assert settled.interstitial_dismissed is True
    assert dismiss.clicked == 1


def test_post_publish_interstitial_absent_is_safe(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo)
    client = FacebookWebClient(settings, repo, FakeChrome(), resolver=FakeResolver())

    dismissed = asyncio.run(client._dismiss_post_publish_interstitials(FakePage(), job_id))

    assert dismissed is False


def test_post_publish_interstitial_without_safe_button_records_diagnostics(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo)
    dialog = FakeLocator()

    class UnsafeResolver(FakeResolver):
        async def exists(self, page: Any, key: str, **_: Any) -> bool:
            if key == "facebook.post_publish_interstitial_dialog":
                return True
            return await super().exists(page, key)

        async def find_first(self, page: Any, key: str, **_: Any) -> FakeLocator:
            if key == "facebook.post_publish_interstitial_dialog":
                return dialog
            if key == "facebook.post_publish_interstitial_dismiss":
                raise KeyError("no safe dismiss button")
            return await super().find_first(page, key)

    page = FakePage()
    client = FacebookWebClient(settings, repo, FakeChrome(), resolver=UnsafeResolver())
    dismissed = asyncio.run(client._dismiss_post_publish_interstitials(page, job_id))

    assert dismissed is False
    assert any("interstitial-no-safe-dismiss" in path for path in page.shots)
    event_types = [event.event_type for event in repo.list_events(job_id)]
    assert "late_interstitial_detected" in event_types
    assert "late_interstitial_dismiss_failed" in event_types


def test_caption_matching_normalizes_unicode_whitespace_and_newlines() -> None:
    approved = "CA LÂM SÀNG\nGhi nhận chính:   Tổn thương giảm âm\nNguồn: CDHA.AI"
    rendered = "CA LÂM SÀNG  Ghi nhận chính:\nTổn thương giảm âm ... Nguồn: CDHA.AI"

    match = FacebookWebClient._caption_match(approved, rendered)

    assert match["matched"] is True
    assert match["score"] >= 6
    assert "prefix" in match["reason"] or "suffix" in match["reason"]


def test_pfbid_permalink_is_normalized_and_id_is_retained() -> None:
    raw = "https://facebook.com/CDHA.AI/posts/pfbid02AbCdEf123?ref=share"
    normalized = FacebookWebClient.normalize_permalink(raw, base_url="https://facebook.com/CDHA.AI")

    assert normalized == "https://www.facebook.com/CDHA.AI/posts/pfbid02AbCdEf123"
    assert FacebookWebClient.extract_post_id(normalized) == "pfbid02AbCdEf123"


def test_direct_post_submit_permalink_verifies_without_success_toast(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    approved_job(repo, job_id="direct-link")

    class Link:
        async def get_attribute(self, name: str) -> str | None:
            assert name == "href"
            return "/test.page/posts/pfbidDirect123?ref=notif"

    class DirectLinkClient(FacebookWebClient):
        async def _all_locators(self, page: Any, key: str) -> list[Any]:
            if key == "facebook.publish_result_permalink":
                return [Link()]
            return []

    client = DirectLinkClient(settings, repo, FakeChrome(), resolver=FakeResolver())
    candidate = asyncio.run(client._find_direct_result_permalink(FakePage(), {"old"}))

    assert candidate is not None
    assert candidate["post_id"] == "pfbidDirect123"
    assert candidate["method"] == "direct-post-submit-permalink"


def _make_uncertain_publication(repo: JobRepository, job_id: str) -> None:
    for status in (
        WorkflowStatus.FACEBOOK_PREPARING,
        WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        WorkflowStatus.FACEBOOK_PUBLISHING,
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
    ):
        repo.transition(job_id, status)
    now = datetime.now(UTC).isoformat()
    repo.update_data(job_id, {
        "facebook_target_url": "https://www.facebook.com/test.page",
        "facebook_post_text": "CA LÂM SÀNG\nNguồn: https://cdha.ai/dash?view=late",
        "facebook_image_paths": ["one.png"],
        "facebook_known_post_ids": ["old"],
        "facebook_publication_started_at": now,
        "facebook_click_timestamp": now,
        "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
        "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
    })


def test_reconciliation_finds_late_post_on_next_attempt_without_toast(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_facebook_reconciliation_retries=3)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="late-reconcile")
    _make_uncertain_publication(repo, job_id)

    class SequencedClient(FacebookWebClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.calls = 0

        async def _ensure_authenticated(self, *_: Any, **__: Any) -> None: return None

        async def _verify_publication(self, *_: Any, **__: Any):
            self.calls += 1
            if self.calls == 1:
                return FacebookPublishResult(
                    False, "PUBLICATION_UNCERTAIN", job_id=job_id,
                    error="not visible yet",
                ), {"success_notification": False, "exact_post_match": False}
            return FacebookPublishResult(
                True,
                "PUBLISHED_VERIFIED",
                target_url=settings.facebook_target_url,
                post_id="pfbidLate123",
                permalink="https://www.facebook.com/test.page/posts/pfbidLate123",
                verification_method="permalink+caption_prefix",
                job_id=job_id,
            ), {"success_notification": False, "exact_post_match": True, "text_match": True}

    client = SequencedClient(settings, repo, FakeChrome(), resolver=FakeResolver())

    class Publisher:
        async def reconcile_publication(self, *, job_id: str) -> FacebookPublishResult:
            return await client.reconcile_interrupted_publication(job_id=job_id)

    use_case = ReconcilePublishUseCase(repo, Publisher())  # type: ignore[arg-type]
    first = asyncio.run(use_case.execute(job_id))
    assert first.success is False
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN
    assert repo.get_job(job_id).data["facebook_reconciliation_attempt"] == 1
    assert repo.get_job(job_id).data["facebook_submission_status"] == "SUBMITTED_UNCONFIRMED"

    second = asyncio.run(use_case.execute(job_id))
    persisted = repo.get_job(job_id)
    assert second.success is True
    assert persisted.status is WorkflowStatus.FACEBOOK_PUBLISHED
    assert persisted.data["facebook_post_id"] == "pfbidLate123"
    assert persisted.data["facebook_post_url"].endswith("/posts/pfbidLate123")
    assert persisted.data["facebook_publication_state"] == "PUBLISHED_CONFIRMED"


def test_reconciliation_exhaustion_is_controlled_and_keeps_submit_evidence(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_facebook_reconciliation_retries=2)
    repo = make_repo(settings)
    job_id = approved_job(repo, job_id="reconcile-exhausted")
    _make_uncertain_publication(repo, job_id)

    class NeverFoundClient(FacebookWebClient):
        async def _ensure_authenticated(self, *_: Any, **__: Any) -> None: return None
        async def _verify_publication(self, *_: Any, **__: Any):
            return FacebookPublishResult(
                False, "PUBLICATION_UNCERTAIN", job_id=job_id, error="not found"
            ), {"success_notification": False, "exact_post_match": False}

    client = NeverFoundClient(settings, repo, FakeChrome(), resolver=FakeResolver())

    class Publisher:
        async def reconcile_publication(self, *, job_id: str) -> FacebookPublishResult:
            return await client.reconcile_interrupted_publication(job_id=job_id)

    use_case = ReconcilePublishUseCase(repo, Publisher())  # type: ignore[arg-type]
    assert asyncio.run(use_case.execute(job_id)).success is False
    assert asyncio.run(use_case.execute(job_id)).success is False

    persisted = repo.get_job(job_id)
    assert persisted.status is WorkflowStatus.FACEBOOK_PUBLISH_FAILED
    assert persisted.data["facebook_reconciliation_attempt"] == 2
    assert persisted.data["facebook_reconciliation_exhausted"] is True
    assert persisted.data["facebook_submission_status"] == "SUBMITTED_UNCONFIRMED"
    assert persisted.data["facebook_publication_state"] == "SUBMITTED_UNCONFIRMED"
    assert Path(persisted.data["facebook_reconciliation_diagnostic_path"]).is_file()
    assert any(
        event.event_type == "reconciliation_exhausted"
        for event in repo.list_events(job_id)
    )


# ---------------------------------------------------------------------------
# Section 7: Verified signals WITHOUT post_id must become UNCERTAIN
# ---------------------------------------------------------------------------

def test_verified_signals_without_post_id_becomes_uncertain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A 'true' success from _verify_publication with no post_id/permalink must
    NOT transition to FACEBOOK_PUBLISHED — it must go to UNCERTAIN."""
    monkeypatch.setattr(
        PostContentService, "validate_image",
        lambda self, image_path: Path(image_path).expanduser().resolve(),
    )
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = approved_job(repo)
    image = make_png(tmp_path / "img.png")

    class NoIdClient(VerifiedPublishClient):
        async def _verify_publication(self, page: Any, job_id: str, text: str,
                                      images: list[Path], started: datetime, before_ids: set[str]):
            # success=True but no post_id / permalink / post_url
            return FacebookPublishResult(
                success=True,
                status="PUBLISHED_VERIFIED",
                target_url=self.settings.facebook_target_url,
                published_at=datetime.now(UTC),
                verification_method="success_notification+composer_closed",
                job_id=job_id,
                # deliberately omit post_id, permalink, post_url
            ), {"success_notification": True, "composer_closed": True}

    client = NoIdClient(
        settings, repo, FakeChrome(), resolver=FakeResolver(), confirmation_provider=lambda _: "1",
    )
    asyncio.run(client.prepare_post(
        target_url=settings.facebook_target_url,
        post_text=_valid_post_text(settings),
        image_paths=[image],
        job_id=job_id,
    ))
    result = asyncio.run(client.publish_prepared_post(job_id=job_id))

    assert result.success is False, "False-success: no durable ID must not count as verified"
    assert result.is_verified is False
    assert repo.get_job(job_id).status is WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN


# ---------------------------------------------------------------------------
# Section 8: Duplicate fingerprint detection
# ---------------------------------------------------------------------------

def test_duplicate_verified_fingerprint_blocks_new_publish(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    svc = PostContentService(settings)
    target = svc.normalize_target_url(settings.facebook_target_url)

    old = repo.create_job("https://facebook.com/reel/old", job_id="old-dup")
    repo.update_data(old.job_id, {
        "facebook_content_hash": "abc123",
        "facebook_target_url": target,
        "facebook_publication_verified": True,
    })

    from app.adapters.facebook_adapter import FacebookPublisherAdapter
    content = PostContentService(settings)
    content.content_fingerprint = lambda *args, **kwargs: "abc123"
    content.select_screenshots = lambda *args, **kwargs: (["mock"], [])
    adapter = FacebookPublisherAdapter(settings, repo, FacebookWebClient(settings, repo, object()), content=content)
    job_id = approved_job(repo, job_id="new-job", source_url="https://facebook.com/reel/old")
    repo.update_data(job_id, {"facebook_target_url": target})
    
    validation = adapter.validate_job(job_id)
    assert not validation.valid
    assert any("Duplicate" in e for e in validation.errors)


def test_duplicate_uncertain_fingerprint_also_blocks(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    svc = PostContentService(settings)
    target = svc.normalize_target_url(settings.facebook_target_url)

    old = repo.create_job("https://facebook.com/reel/old", job_id="uncertain-old")
    repo.update_data(old.job_id, {
        "facebook_content_hash": "hash-uncertain",
        "facebook_target_url": target,
        "facebook_publication_uncertain": True,
    })

    from app.adapters.facebook_adapter import FacebookPublisherAdapter
    content = PostContentService(settings)
    content.content_fingerprint = lambda *args, **kwargs: "hash-uncertain"
    content.select_screenshots = lambda *args, **kwargs: (["mock"], [])
    adapter = FacebookPublisherAdapter(settings, repo, FacebookWebClient(settings, repo, object()), content=content)
    job_id = approved_job(repo, job_id="new-job", source_url="https://facebook.com/reel/old")
    repo.update_data(job_id, {"facebook_target_url": target})

    validation = adapter.validate_job(job_id)
    assert not validation.valid
    assert any("Duplicate" in e for e in validation.errors)


def test_submitting_state_is_treated_as_possible_submit_for_duplicate_safety() -> None:
    assert FacebookWebClient._publication_was_submitted(
        {"facebook_submission_status": "SUBMITTING"}
    )
