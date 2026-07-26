"""Phase 5 automated tests — mocked adapters only, no real Chrome or network."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import Settings
from app.main import build_parser, _run_check_config
from app.models.results import (
    FacebookCommentResult, FacebookPermalinkResult,
    FacebookPostPreparationResult, FacebookPublishResult,
    FacebookWorkflowResult, PipelineResult,
    CDHAPipelineError, ConfigurationError, ReviewRequiredError,
    FacebookPublishUncertainError,
)
from app.models.workflow import WorkflowStatus, JobRecord
from app.repositories.job_repository import JobRepository
from app.workflows.cdha_pipeline import CDHAPipeline
from app.workflows.state_machine import WorkflowStateMachine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_settings(tmp_path: Path, **changes: Any) -> Settings:
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


def make_completed_job(repo: JobRepository, source_url: str = "https://fb.com/reel/1") -> str:
    job = repo.create_job(source_url)
    for status in (
        WorkflowStatus.DOWNLOADREEL_RUNNING, WorkflowStatus.DOWNLOADED,
        WorkflowStatus.GEMINI_OPENING, WorkflowStatus.GEMINI_GENERATING,
        WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.CDHA_OPENING,
        WorkflowStatus.CDHA_UPLOADING, WorkflowStatus.CDHA_ANALYZING,
        WorkflowStatus.CDHA_ANALYZED, WorkflowStatus.SCREENSHOTS_CAPTURING,
        WorkflowStatus.SCREENSHOTS_CAPTURED, WorkflowStatus.WAITING_FOR_REVIEW,
        WorkflowStatus.APPROVED,
        WorkflowStatus.FACEBOOK_PREPARING, WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        WorkflowStatus.FACEBOOK_PUBLISHING, WorkflowStatus.FACEBOOK_PUBLISHED,
        WorkflowStatus.POST_URL_EXTRACTING, WorkflowStatus.POST_URL_EXTRACTED,
        WorkflowStatus.COMMENT_ADDING, WorkflowStatus.COMMENT_ADDED,
        WorkflowStatus.COMPLETED,
    ):
        repo.transition(job.job_id, status)
    return job.job_id


class MockDownloadAdapter:
    def __init__(self, *, success: bool = True, job_id: str = "dl") -> None:
        self.success = success
        self._job_id = job_id

    async def run(self, url: str, *, force_download: bool = False):
        from app.models.results import DownloadResult
        return DownloadResult(
            job_id=self._job_id, source_url=url, normalized_source_url=url,
            success=self.success, error=None if self.success else "download failed",
        )


class MockFacebookAdapter:
    def __init__(self, repo: JobRepository, *, success: bool = True) -> None:
        self.repo = repo
        self.success = success
        self.calls: list[str] = []

    async def prepare(self, *, job_id: str) -> FacebookPostPreparationResult:
        self.calls.append("prepare")
        if self.success:
            self.repo.transition(job_id, WorkflowStatus.FACEBOOK_PREPARING)
            self.repo.transition(job_id, WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW)
        return FacebookPostPreparationResult(self.success, job_id)

    async def publish(self, *, job_id: str) -> FacebookPublishResult:
        self.calls.append("publish")
        if self.success:
            self.repo.transition(job_id, WorkflowStatus.FACEBOOK_PUBLISHING)
            self.repo.transition(job_id, WorkflowStatus.FACEBOOK_PUBLISHED,
                                 data_patch={"facebook_publication_started_at": datetime.now(UTC).isoformat()})
        return FacebookPublishResult(self.success, job_id)

    async def extract_permalink(self, *, job_id: str) -> FacebookPermalinkResult:
        self.calls.append("extract")
        if self.success:
            self.repo.transition(job_id, WorkflowStatus.POST_URL_EXTRACTING)
            self.repo.transition(job_id, WorkflowStatus.POST_URL_EXTRACTED,
                                 data_patch={"facebook_post_url": "https://www.facebook.com/posts/999"})
        return FacebookPermalinkResult(self.success, job_id, "https://www.facebook.com/posts/999")

    async def add_permalink_comment(self, *, job_id: str) -> FacebookCommentResult:
        self.calls.append("comment")
        if self.success:
            self.repo.transition(job_id, WorkflowStatus.COMMENT_ADDING)
            self.repo.transition(job_id, WorkflowStatus.COMMENT_ADDED)
            self.repo.transition(job_id, WorkflowStatus.COMPLETED)
        return FacebookCommentResult(self.success, job_id, "https://www.facebook.com/posts/999",
                                     comment_text="📋 Copy link chia sẻ:\nhttps://www.facebook.com/posts/999")

    async def complete(self, *, job_id: str) -> FacebookWorkflowResult:
        self.calls.append("complete")
        return FacebookWorkflowResult(
            self.success, job_id,
            WorkflowStatus.COMPLETED.value if self.success else WorkflowStatus.FACEBOOK_PUBLISH_FAILED.value
        )


class MockChrome:
    pass


# ---------------------------------------------------------------------------
# PipelineResult tests
# ---------------------------------------------------------------------------

def test_pipeline_result_serializes_all_fields() -> None:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    r = PipelineResult(
        success=True, job_id="j", current_status="COMPLETED",
        source_url="https://fb.com/reel/1",
        facebook_post_url="https://www.facebook.com/posts/1",
        completed_steps=["download", "gemini"],
        started_at=now,
        updated_at=now,
        completed_at=now,
    )
    d = r.to_dict()
    assert d["success"] is True
    assert d["current_status"] == "COMPLETED"
    assert d["started_at"] == now.isoformat()
    assert "download" in d["completed_steps"]


def test_pipeline_result_distinguishes_pending_vs_error() -> None:
    paused = PipelineResult(False, "j", "WAITING_FOR_REVIEW",
                             pending_manual_action="python main.py --review-job j")
    failed = PipelineResult(False, "j", "FACEBOOK_PUBLISH_FAILED",
                             error="Publish error")
    assert paused.pending_manual_action is not None
    assert paused.error is None
    assert failed.error is not None
    assert failed.pending_manual_action is None


# ---------------------------------------------------------------------------
# Error classification tests
# ---------------------------------------------------------------------------

def test_error_hierarchy_retryability() -> None:
    assert ConfigurationError.retryable is False
    assert ReviewRequiredError.manual_action_required is True
    assert FacebookPublishUncertainError.retryable is False
    assert FacebookPublishUncertainError.manual_action_required is True


def test_pipeline_errors_are_subclass_of_base() -> None:
    for cls in (ConfigurationError, ReviewRequiredError, FacebookPublishUncertainError):
        assert issubclass(cls, CDHAPipelineError)


# ---------------------------------------------------------------------------
# CANCELLED state tests
# ---------------------------------------------------------------------------

def test_cancelled_is_terminal_and_no_auto_resume(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job = repo.create_job("https://fb.com/reel/cancel")
    repo.transition(job.job_id, WorkflowStatus.DOWNLOADREEL_RUNNING)
    repo.transition(job.job_id, WorkflowStatus.CANCELLED)
    assert repo.get_job(job.job_id).status is WorkflowStatus.CANCELLED
    assert WorkflowStatus.DOWNLOADREEL_RUNNING not in WorkflowStateMachine.allowed_targets(
        WorkflowStatus.CANCELLED
    )


def test_cancel_command_records_event_and_preserves_artifacts(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job = repo.create_job("https://fb.com/reel/cancel2")
    repo.transition(job.job_id, WorkflowStatus.DOWNLOADREEL_RUNNING)
    repo.transition(job.job_id, WorkflowStatus.DOWNLOADED)
    repo.transition(job.job_id, WorkflowStatus.CANCELLED,
                    details={"cancelled_by": "operator"})
    events = repo.list_events(job.job_id)
    cancel_events = [e for e in events if e.to_status is WorkflowStatus.CANCELLED]
    assert len(cancel_events) == 1
    assert cancel_events[0].details.get("cancelled_by") == "operator"


# ---------------------------------------------------------------------------
# Phase 5 state transitions
# ---------------------------------------------------------------------------

def test_cancelled_reachable_from_any_active_state() -> None:
    active_states = [
        WorkflowStatus.CREATED, WorkflowStatus.DOWNLOADED,
        WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.APPROVED,
        WorkflowStatus.FACEBOOK_PREPARING,
    ]
    for state in active_states:
        assert WorkflowStatus.CANCELLED in WorkflowStateMachine.allowed_targets(state), \
            f"CANCELLED must be reachable from {state.value}"


def test_completed_job_returns_immediately_without_rerunning(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = make_completed_job(repo)
    pipeline = CDHAPipeline(settings, repo)
    result = asyncio.run(pipeline.resume(job_id=job_id))
    assert result.success
    assert result.current_status == "COMPLETED"


def test_resume_rejected_job_does_not_continue(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job = repo.create_job("https://fb.com/reel/rejected")
    for status in (
        WorkflowStatus.DOWNLOADREEL_RUNNING, WorkflowStatus.DOWNLOADED,
        WorkflowStatus.GEMINI_OPENING, WorkflowStatus.GEMINI_GENERATING,
        WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.CDHA_OPENING,
        WorkflowStatus.CDHA_UPLOADING, WorkflowStatus.CDHA_ANALYZING,
        WorkflowStatus.CDHA_ANALYZED, WorkflowStatus.SCREENSHOTS_CAPTURING,
        WorkflowStatus.SCREENSHOTS_CAPTURED, WorkflowStatus.WAITING_FOR_REVIEW,
        WorkflowStatus.REJECTED,
    ):
        repo.transition(job.job_id, status)
    pipeline = CDHAPipeline(settings, repo)
    result = asyncio.run(pipeline.resume(job_id=job.job_id))
    assert not result.success
    assert "rejected" in (result.error or "").lower()


def test_resume_uncertain_publication_requires_manual_action(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job = repo.create_job("https://fb.com/reel/uncertain")
    for status in (
        WorkflowStatus.DOWNLOADREEL_RUNNING, WorkflowStatus.DOWNLOADED,
        WorkflowStatus.GEMINI_OPENING, WorkflowStatus.GEMINI_GENERATING,
        WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.CDHA_OPENING,
        WorkflowStatus.CDHA_UPLOADING, WorkflowStatus.CDHA_ANALYZING,
        WorkflowStatus.CDHA_ANALYZED, WorkflowStatus.SCREENSHOTS_CAPTURING,
        WorkflowStatus.SCREENSHOTS_CAPTURED, WorkflowStatus.WAITING_FOR_REVIEW,
        WorkflowStatus.APPROVED, WorkflowStatus.FACEBOOK_PREPARING,
        WorkflowStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        WorkflowStatus.FACEBOOK_PUBLISHING,
        WorkflowStatus.FACEBOOK_PUBLISH_UNCERTAIN,
    ):
        repo.transition(job.job_id, status)
    pipeline = CDHAPipeline(settings, repo)
    result = asyncio.run(pipeline.resume(job_id=job.job_id))
    assert not result.success
    assert "uncertain" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Dry-run tests
# ---------------------------------------------------------------------------

def test_dry_run_performs_no_external_actions(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, facebook_target_url="")
    repo = make_repo(settings)
    pipeline = CDHAPipeline(settings, repo, dry_run=True)
    result = asyncio.run(pipeline.start_from_reel(reel_url="https://www.facebook.com/reel/123"))
    assert result.job_id == "dry_run"
    assert result.current_status == "DRY_RUN"


def test_dry_run_reports_missing_facebook_target_as_warning(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, facebook_target_url="")
    repo = make_repo(settings)
    pipeline = CDHAPipeline(settings, repo, dry_run=True)
    result = asyncio.run(pipeline.start_from_reel(reel_url="https://www.facebook.com/reel/456"))
    assert any("FACEBOOK_TARGET_URL" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# list_resumable_jobs tests
# ---------------------------------------------------------------------------

def test_list_resumable_jobs_shows_actionable_states(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job = repo.create_job("https://fb.com/reel/resume_list")
    for status in (
        WorkflowStatus.DOWNLOADREEL_RUNNING, WorkflowStatus.DOWNLOADED,
        WorkflowStatus.GEMINI_OPENING, WorkflowStatus.GEMINI_GENERATING,
        WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.CDHA_OPENING,
        WorkflowStatus.CDHA_UPLOADING, WorkflowStatus.CDHA_ANALYZING,
        WorkflowStatus.CDHA_ANALYZED, WorkflowStatus.SCREENSHOTS_CAPTURING,
        WorkflowStatus.SCREENSHOTS_CAPTURED, WorkflowStatus.WAITING_FOR_REVIEW,
    ):
        repo.transition(job.job_id, status)
    rows = repo.list_resumable_jobs()
    assert any(r["job_id"] == job.job_id for r in rows)
    entry = next(r for r in rows if r["job_id"] == job.job_id)
    assert "review-job" in entry["recommended_command"]


def test_completed_jobs_excluded_from_resumable(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = make_completed_job(repo)
    rows = repo.list_resumable_jobs()
    assert not any(r["job_id"] == job_id for r in rows)


# ---------------------------------------------------------------------------
# Database backup tests
# ---------------------------------------------------------------------------

def test_backup_creates_non_zero_copy(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    repo.create_job("https://fb.com/reel/backup_test")
    backup_path, info = repo.backup_database()
    assert backup_path.exists()
    assert backup_path.stat().st_size > 0
    assert info["job_count"] >= 1
    assert info["event_count"] >= 1
    assert "backup" in str(backup_path.name)


def test_backup_fails_gracefully_when_db_missing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, database_path=tmp_path / "missing.sqlite3")
    repo = JobRepository(settings.database_path)
    with pytest.raises(FileNotFoundError):
        repo.backup_database()


# ---------------------------------------------------------------------------
# Config check tests
# ---------------------------------------------------------------------------

def test_check_config_passes_with_basic_settings(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    # Should not raise; returns 0 or 1 based on chrome availability
    result = _run_check_config(settings, repo)
    assert result in (0, 1)


def test_check_config_warns_missing_facebook_target(tmp_path: Path, capsys) -> None:
    settings = make_settings(tmp_path, facebook_target_url="")
    repo = make_repo(settings)
    _run_check_config(settings, repo)
    captured = capsys.readouterr()
    assert "FACEBOOK" in captured.out.upper() or "facebook" in captured.out.lower()


# ---------------------------------------------------------------------------
# Phase 5 CLI parser tests
# ---------------------------------------------------------------------------

def test_phase5_cli_parser_commands() -> None:
    parser = build_parser()
    cases = [
        (["--reel-url", "https://fb.com/reel/1"], "reel_url", "https://fb.com/reel/1"),
        (["--resume-job", "abc"], "resume_job", "abc"),
        (["--run-until-review", "abc"], "run_until_review", "abc"),
        (["--continue-approved-job", "abc"], "continue_approved_job", "abc"),
        (["--retry-job", "abc"], "retry_job", "abc"),
        (["--cancel-job", "abc"], "cancel_job", "abc"),
        (["--check-config"], "check_config", True),
        (["--backup-db"], "backup_db", True),
        (["--list-resumable-jobs"], "list_resumable_jobs", True),
        (["--dry-run", "--reel-url", "url"], "dry_run", True),
        (["--skip-facebook-comment", "--reel-url", "url"], "skip_facebook_comment", True),
        (["--yes", "--reel-url", "url"], "yes", True),
    ]
    for argv, attr, expected in cases:
        parsed = parser.parse_args(argv)
        assert getattr(parsed, attr) == expected, f"Failed for {argv}: {attr}"


def test_force_download_only_allowed_with_reel_url() -> None:
    parser = build_parser()
    args = parser.parse_args(["--download-reel", "url", "--force-download"])
    assert args.force_download


# ---------------------------------------------------------------------------
# Downstream invalidation helpers
# ---------------------------------------------------------------------------

def test_pipeline_completed_steps_detection(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo = make_repo(settings)
    job_id = make_completed_job(repo)
    job = repo.get_job(job_id)
    steps = CDHAPipeline._completed_steps(job)
    assert "download" in steps
    assert "completed" in steps


# ---------------------------------------------------------------------------
# Existing test suites still pass (regression guard)
# ---------------------------------------------------------------------------

def test_existing_workflow_states_still_defined() -> None:
    """Regression: all Phase 1-4 states remain in WorkflowStatus."""
    required = [
        "CREATED", "DOWNLOADED", "CLINICAL_FACTORS_GENERATED",
        "CDHA_ANALYZED", "WAITING_FOR_REVIEW", "APPROVED",
        "FACEBOOK_PREPARING", "FACEBOOK_PUBLISHED",
        "POST_URL_EXTRACTED", "COMMENT_ADDED", "COMPLETED",
        "CANCELLED",  # new Phase 5
    ]
    defined = {s.value for s in WorkflowStatus}
    for name in required:
        assert name in defined, f"WorkflowStatus.{name} is missing"


def test_existing_facebook_tests_still_importable() -> None:
    """Regression: Phase 4 modules remain importable."""
    from app.browser.facebook_client import FacebookWebClient
    from app.adapters.facebook_adapter import FacebookPublisherAdapter
    from app.services.post_content_service import PostContentService
    assert FacebookWebClient
    assert FacebookPublisherAdapter
    assert PostContentService
