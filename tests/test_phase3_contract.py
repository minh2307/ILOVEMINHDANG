from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.browser.cdha_client import CDHAWebClient
from app.browser.gemini_client import GeminiWebClient
from app.browser.selector_resolver import SelectorResolver
from app.config.settings import Settings
from app.main import _run_login_setup, build_parser
from app.models.results import CDHAAnalysisResult, ClinicalFactorsResult
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.clinical_factors_service import ClinicalFactorsService
from app.services.privacy_service import MEDICAL_RECORD_TOKEN, PrivacyService
from app.services.review_service import ReviewService


def settings_for(tmp_path: Path) -> Settings:
    return replace(
        Settings.from_env(env_file=tmp_path / "missing.env"),
        database_path=tmp_path / "jobs.sqlite3",
        job_data_dir=tmp_path / "jobs",
        chrome_profile_dir=tmp_path / "profile",
    )


def valid_factors() -> str:
    return "\n".join(
        [
            "Cơ quan/vùng khảo sát: Gan",
            "Triệu chứng chính: Đau hạ sườn phải",
            "Thời gian xuất hiện triệu chứng: Không được cung cấp",
            "Chỉ định hoặc nghi ngờ lâm sàng: Khảo sát gan",
            "Tiền sử liên quan: Không được cung cấp",
            "Kết quả xét nghiệm liên quan: Không được cung cấp",
            "Thông tin bổ sung: Không được cung cấp",
            "Thông tin chưa được cung cấp: Tuổi, giới",
        ]
    )


def waiting_job(repository: JobRepository) -> str:
    job = repository.create_job("https://facebook.com/reel/phase3")
    for status in (
        WorkflowStatus.DOWNLOADREEL_RUNNING,
        WorkflowStatus.DOWNLOADED,
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
        repository.transition(job.job_id, status)
    repository.update_data(job.job_id, {
        "cdha_result": {
            "key_findings": ["Ghi nhận tổn thương giảm âm."],
            "impression": "Hình ảnh gợi ý tổn thương, cần đối chiếu lâm sàng.",
            "analysis_url": "https://cdha.ai/dash?view=phase3-result",
        },
        "cdha_view_url": "https://cdha.ai/dash?view=phase3-result",
    })
    return job.job_id


def test_phase3_settings_match_approved_defaults(tmp_path: Path) -> None:
    settings = Settings.from_env(env_file=tmp_path / "missing.env")

    assert settings.cdha_poll_interval_seconds == 3
    assert settings.cdha_result_stability_seconds == 5
    assert settings.clinical_factors_max_comments == 100
    assert settings.gemini_comment_total_max_chars == 15_000
    assert settings.gemini_prompt_max_chars == 30_000
    assert settings.clinical_factors_max_chars == 5_000


def test_prompt_filters_facebook_ui_and_limits_total_comment_characters() -> None:
    service = ClinicalFactorsService(
        max_comment_chars=100, max_comments=10, max_total_comment_chars=8
    )
    prompt = service.build_prompt("caption", ["Like", "123456", "abcdef"])
    comments = prompt.split("Visible public comments:\n\n", 1)[1].split("\n</UNTRUSTED_FACEBOOK_CONTENT>", 1)[0]

    assert "Like" not in comments
    assert comments == "- 123456\n- a…"


def test_result_models_serialize_datetime_and_all_required_paths() -> None:
    now = datetime(2026, 7, 23, tzinfo=UTC)
    clinical = ClinicalFactorsResult(
        success=True,
        job_id="j",
        masked_text="safe",
        raw_response_path=Path("raw.txt"),
        normalized_output_path=Path("normalized.txt"),
        generated_at=now,
    ).to_dict()
    cdha = CDHAAnalysisResult(
        success=True,
        job_id="j",
        diagnostic_screenshot_path=Path("diagnostic.png"),
        started_at=now,
        completed_at=now,
    ).to_dict()

    assert clinical["raw_response_path"] == "raw.txt"
    assert clinical["generated_at"] == now.isoformat()
    assert cdha["diagnostic_screenshot_path"] == "diagnostic.png"
    assert cdha["completed_at"] == now.isoformat()


def test_medical_record_masking_preserves_gestational_age() -> None:
    source = "MRN: AB-12345; thai 12 tuần 3 ngày; CRL 58.2 mm"
    masked = PrivacyService().mask(source)

    assert MEDICAL_RECORD_TOKEN in masked
    assert "AB-12345" not in masked
    assert "12 tuần 3 ngày" in masked
    assert "58.2 mm" in masked


class LabelLocator:
    @property
    def first(self) -> "LabelLocator":
        return self

    async def wait_for(self, **_: Any) -> None:
        return None


class LabelPage:
    url = "https://fixture.invalid"

    def get_by_label(self, label: str, *, exact: bool) -> LabelLocator:
        assert label == "Clinical Factors"
        assert exact
        return LabelLocator()

    async def title(self) -> str:
        return "Fixture"


def test_selector_resolver_supports_accessible_labels(tmp_path: Path) -> None:
    config = tmp_path / "selectors.yaml"
    config.write_text(
        "cdha:\n  factors:\n    - label: Clinical Factors\n      exact: true\n",
        encoding="utf-8",
    )
    locator = asyncio.run(SelectorResolver(config).find_first(LabelPage(), "cdha.factors"))
    assert isinstance(locator, LabelLocator)


def test_review_approve_only_marks_job_for_later_phase(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    repository = JobRepository(settings.database_path)
    repository.initialize()
    job_id = waiting_job(repository)

    decision = ReviewService(settings, repository).review(
        job_id, choice_provider=lambda _: "1"
    )

    assert decision.action == "approved"
    assert repository.get_job(job_id).status is WorkflowStatus.APPROVED
    assert all("FACEBOOK" not in event.to_status.value for event in repository.list_events(job_id))


def test_review_cannot_approve_label_only_cdha_summary(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    repository = JobRepository(settings.database_path)
    repository.initialize()
    job_id = waiting_job(repository)
    repository.update_data(job_id, {
        "cdha_result": {
            "key_findings": ["Key findings:"],
            "impression": "Impression:",
        }
    })

    with pytest.raises(ValueError, match="Key Findings"):
        ReviewService(settings, repository).review(
            job_id, choice_provider=lambda _: "1"
        )

    assert repository.get_job(job_id).status is WorkflowStatus.WAITING_FOR_REVIEW


def test_review_edit_masks_and_queues_valid_cdha_retry(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    repository = JobRepository(settings.database_path)
    repository.initialize()
    job_id = waiting_job(repository)

    decision = ReviewService(settings, repository).review(
        job_id,
        choice_provider=lambda _: "3",
        edited_text_provider=lambda: valid_factors().replace(
            "Gan", "Gan; liên hệ 0901234567", 1
        ),
    )

    job = repository.get_job(job_id)
    assert decision.action == "retry_cdha"
    assert job.status is WorkflowStatus.RETRY_PENDING
    assert "0901234567" not in job.data["clinical_factors"]
    assert Path(job.data["clinical_factors_path"]).is_file()


def test_review_cli_parser_command() -> None:
    args = build_parser().parse_args(["--review-job", "job-1"])
    assert args.review_job == "job-1"


class SetupPage:
    def __init__(self) -> None:
        self.visited: list[str] = []

    async def goto(self, url: str, **_: Any) -> None:
        self.visited.append(url)


class SetupChrome:
    def __init__(self) -> None:
        self.pages = [SetupPage(), SetupPage()]

    async def new_page(self) -> SetupPage:
        return self.pages.pop(0)


class AuthenticatedClient:
    async def is_authenticated(self, page: Any) -> bool:
        return True


def test_login_setup_only_navigates_and_verifies(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    chrome = SetupChrome()

    result = asyncio.run(
        _run_login_setup(settings, chrome, AuthenticatedClient())
    )

    assert result == 0


class ManualPage:
    url = "https://manual.invalid"

    async def goto(self, *_: Any, **__: Any) -> None:
        return None

    async def screenshot(self, *, path: str, **_: Any) -> None:
        Path(path).write_bytes(b"png")

    async def content(self) -> str:
        return "<html>manual action required</html>"

    async def title(self) -> str:
        return "Manual action"


class ManualChrome:
    def __init__(self) -> None:
        self.page = ManualPage()

    async def new_page(self) -> ManualPage:
        return self.page

    async def wait_for_manual_action(self, *_: Any, **__: Any) -> None:
        raise RuntimeError("manual action not confirmed")

    async def save_diagnostics(
        self, page: Any, output_dir: Path, name: str
    ) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        screenshot = output_dir / f"{name}.png"
        html = output_dir / f"{name}.html"
        screenshot.write_bytes(b"png")
        html.write_text(await page.content(), encoding="utf-8")
        return screenshot, html


class LoginRequiredResolver:
    async def exists(self, page: Any, key: str, **_: Any) -> bool:
        return key.endswith("login_markers")


def test_unconfirmed_gemini_login_remains_resumable(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    repository = JobRepository(settings.database_path)
    repository.initialize()
    job = repository.create_job("https://facebook.com/reel/gemini-login")
    repository.transition(job.job_id, WorkflowStatus.DOWNLOADREEL_RUNNING)
    repository.transition(job.job_id, WorkflowStatus.DOWNLOADED)
    client = GeminiWebClient(
        settings, repository, ManualChrome(), resolver=LoginRequiredResolver()
    )

    result = asyncio.run(
        client.generate_clinical_factors(
            caption="caption", comments=[], job_id=job.job_id
        )
    )

    assert not result.success
    assert repository.get_job(job.job_id).status is WorkflowStatus.NEEDS_GEMINI_LOGIN


def test_unconfirmed_cdha_login_remains_resumable(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    repository = JobRepository(settings.database_path)
    repository.initialize()
    job = repository.create_job("https://facebook.com/reel/cdha-login")
    for status in (
        WorkflowStatus.DOWNLOADREEL_RUNNING,
        WorkflowStatus.DOWNLOADED,
        WorkflowStatus.GEMINI_OPENING,
        WorkflowStatus.GEMINI_GENERATING,
        WorkflowStatus.CLINICAL_FACTORS_GENERATED,
    ):
        repository.transition(job.job_id, status)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"synthetic")
    client = CDHAWebClient(
        settings, repository, ManualChrome(), resolver=LoginRequiredResolver()
    )

    result = asyncio.run(
        client.analyze_video(
            video_path=video, clinical_factors=valid_factors(), job_id=job.job_id
        )
    )

    assert not result.success
    assert repository.get_job(job.job_id).status is WorkflowStatus.NEEDS_CDHA_LOGIN
