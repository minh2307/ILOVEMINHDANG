from __future__ import annotations

import pytest

from app.application.dto.stage_execution_result import StageExecutionResult
from app.application.use_cases.process_job_use_case import ProcessJobUseCase
from app.domain.enums.job_status import JobStatus
from app.repositories.job_repository import JobRepository


class FakeStages:
    def __init__(self, repository: JobRepository) -> None:
        self.repository = repository
        self.calls: list[str] = []

    async def download(self, job_id: str) -> StageExecutionResult:
        self.calls.append("download")
        self.repository.transition(job_id, JobStatus.DOWNLOADREEL_RUNNING)
        self.repository.transition(job_id, JobStatus.DOWNLOADED)
        return StageExecutionResult(True)

    async def analyze(self, job_id: str) -> StageExecutionResult:
        self.calls.append("analyze")
        self.repository.transition(job_id, JobStatus.AI_ANALYZING)
        self.repository.transition(job_id, JobStatus.CLINICAL_FACTORS_GENERATED)
        return StageExecutionResult(True)

    async def analyze_cdha(self, job_id: str) -> StageExecutionResult:
        self.calls.append("cdha")
        self.repository.transition(job_id, JobStatus.CDHA_OPENING)
        self.repository.transition(job_id, JobStatus.CDHA_UPLOADING)
        self.repository.transition(job_id, JobStatus.CDHA_ANALYZING)
        self.repository.transition(job_id, JobStatus.CDHA_ANALYZED)
        return StageExecutionResult(True)

    async def capture_screenshots(self, job_id: str) -> StageExecutionResult:
        self.calls.append("screenshots")
        self.repository.transition(job_id, JobStatus.SCREENSHOTS_CAPTURING)
        self.repository.transition(job_id, JobStatus.SCREENSHOTS_CAPTURED)
        return StageExecutionResult(True)

    async def approve_review(self, job_id: str) -> StageExecutionResult:
        self.calls.append("auto_approve")
        self.repository.transition(job_id, JobStatus.APPROVED)
        return StageExecutionResult(True)

    async def facebook(self, job_id: str) -> StageExecutionResult:
        status = self.repository.get_job(job_id).status
        if status is JobStatus.APPROVED:
            self.calls.append("facebook_prepare")
            self.repository.transition(job_id, JobStatus.FACEBOOK_PREPARING)
            self.repository.transition(
                job_id, JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW
            )
        else:
            self.calls.append("facebook_publish")
            self.repository.transition(job_id, JobStatus.FACEBOOK_PUBLISHING)
            self.repository.transition(job_id, JobStatus.FACEBOOK_PUBLISHED)
        return StageExecutionResult(True)

    async def reconcile_facebook(self, job_id: str) -> StageExecutionResult:
        self.calls.append("facebook_reconcile")
        if self.repository.get_job(job_id).status is JobStatus.FACEBOOK_PUBLISH_UNCERTAIN:
            self.repository.transition(
                job_id, JobStatus.PUBLISH_RECONCILIATION_REQUIRED
            )
        self.repository.transition(job_id, JobStatus.FACEBOOK_PUBLISHED)
        return StageExecutionResult(True)

    async def extract_permalink(self, job_id: str) -> StageExecutionResult:
        self.calls.append("permalink")
        self.repository.transition(job_id, JobStatus.POST_URL_EXTRACTING)
        self.repository.transition(job_id, JobStatus.POST_URL_EXTRACTED)
        return StageExecutionResult(True)

    async def add_permalink_comment(self, job_id: str) -> StageExecutionResult:
        self.calls.append("comment")
        self.repository.transition(job_id, JobStatus.COMMENT_ADDING)
        self.repository.transition(job_id, JobStatus.COMMENT_ADDED)
        return StageExecutionResult(True)


@pytest.fixture
def repository(tmp_path):
    repo = JobRepository(tmp_path / "workflow.sqlite3")
    repo.initialize()
    return repo


@pytest.mark.asyncio
async def test_full_workflow_stops_at_both_manual_gates_and_resumes(repository):
    repository.create_job("https://www.facebook.com/reel/123", job_id="job-1")
    stages = FakeStages(repository)
    use_case = ProcessJobUseCase(repository, stages)

    before_review = await use_case.execute("job-1")
    assert before_review.success is True
    assert repository.get_job("job-1").status is JobStatus.WAITING_FOR_REVIEW
    assert stages.calls == ["download", "analyze", "cdha", "screenshots"]

    repository.transition("job-1", JobStatus.APPROVED)
    before_publish = await use_case.execute("job-1")
    assert before_publish.success is True
    assert repository.get_job("job-1").status is JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW
    assert stages.calls[-1] == "facebook_prepare"

    completed = await use_case.execute("job-1", allow_facebook_publish=True)
    assert completed.success is True
    assert repository.get_job("job-1").status is JobStatus.COMPLETED
    assert stages.calls[-3:] == ["facebook_publish", "permalink", "comment"]


@pytest.mark.asyncio
async def test_configured_automatic_workflow_skips_both_manual_gates(repository):
    repository.create_job("https://www.facebook.com/reel/auto", job_id="job-auto")
    stages = FakeStages(repository)
    use_case = ProcessJobUseCase(
        repository,
        stages,
        auto_approve_review=True,
        require_facebook_confirmation=False,
    )

    result = await use_case.execute("job-auto")

    assert result.success is True
    assert repository.get_job("job-auto").status is JobStatus.COMPLETED
    assert result.data["pending_manual_action"] is False
    assert stages.calls == [
        "download",
        "analyze",
        "cdha",
        "screenshots",
        "auto_approve",
        "facebook_prepare",
        "facebook_publish",
        "permalink",
        "comment",
    ]


@pytest.mark.asyncio
async def test_interrupted_stage_is_recorded_then_retried(repository):
    repository.create_job("https://www.facebook.com/reel/456", job_id="job-2")
    repository.transition("job-2", JobStatus.DOWNLOADREEL_RUNNING)
    stages = FakeStages(repository)
    use_case = ProcessJobUseCase(repository, stages)

    result = await use_case.execute("job-2")

    assert result.success is True
    assert repository.get_job("job-2").status is JobStatus.WAITING_FOR_REVIEW
    assert stages.calls[0] == "download"
    recovery_events = [
        event for event in repository.list_events("job-2")
        if event.event_type == "JOB_RECOVERED"
    ]
    assert len(recovery_events) == 1
    assert recovery_events[0].details["reason"] == "interrupted_stage_recovery"


@pytest.mark.asyncio
async def test_submitted_unconfirmed_resumes_with_reconciliation_not_publish(repository):
    repository.create_job("https://www.facebook.com/reel/late", job_id="job-late")
    for status in (
        JobStatus.DOWNLOADREEL_RUNNING, JobStatus.DOWNLOADED,
        JobStatus.AI_ANALYZING, JobStatus.CLINICAL_FACTORS_GENERATED,
        JobStatus.CDHA_OPENING, JobStatus.CDHA_UPLOADING,
        JobStatus.CDHA_ANALYZING, JobStatus.CDHA_ANALYZED,
        JobStatus.SCREENSHOTS_CAPTURING, JobStatus.SCREENSHOTS_CAPTURED,
        JobStatus.WAITING_FOR_REVIEW, JobStatus.APPROVED,
        JobStatus.FACEBOOK_PREPARING,
        JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
        JobStatus.FACEBOOK_PUBLISHING,
        JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
    ):
        repository.transition("job-late", status)
    repository.update_data("job-late", {
        "facebook_submission_status": "SUBMITTED_UNCONFIRMED",
        "facebook_publication_state": "SUBMITTED_UNCONFIRMED",
    })
    stages = FakeStages(repository)

    result = await ProcessJobUseCase(repository, stages).execute("job-late")

    assert result.success is True
    assert repository.get_job("job-late").status is JobStatus.COMPLETED
    assert stages.calls == ["facebook_reconcile", "permalink", "comment"]
    assert "facebook_publish" not in stages.calls


@pytest.mark.asyncio
async def test_stage_failure_without_transition_is_persisted(repository):
    repository.create_job("https://www.facebook.com/reel/999", job_id="job-failure")
    stages = FakeStages(repository)

    async def fail_download(job_id: str) -> StageExecutionResult:
        return StageExecutionResult(False, error="download adapter failed early")

    stages.download = fail_download
    result = await ProcessJobUseCase(repository, stages).execute("job-failure")

    assert result.success is False
    persisted = repository.get_job("job-failure")
    assert persisted.status is JobStatus.FAILED
    assert persisted.error_code == "STAGE_FAILED_WITHOUT_TRANSITION"
    assert persisted.error_message == "download adapter failed early"
