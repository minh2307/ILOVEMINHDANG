from __future__ import annotations

from app.application.dto.stage_execution_result import StageExecutionResult
from app.application.ports.job_repository_port import JobRepositoryPort
from app.application.ports.workflow_stage_port import WorkflowStagePort
from app.domain.enums.job_status import JobStatus
from app.domain.models.job_result import JobResult


class ProcessJobUseCase:
    """The sole state-aware orchestrator for an end-to-end workflow job."""

    _MANUAL_BOUNDARIES = frozenset(
        {
            JobStatus.WAITING_FOR_AUTH_REVIEW,
            JobStatus.BLOCKED,
            JobStatus.REJECTED,
            JobStatus.CANCELLED,
        }
    )
    _FAILURES = frozenset(
        {
            JobStatus.DOWNLOADREEL_FAILED,
            JobStatus.GEMINI_FAILED,
            JobStatus.AI_FAILED,
            JobStatus.CDHA_FAILED,
            JobStatus.FACEBOOK_PUBLISH_FAILED,
            JobStatus.POST_URL_EXTRACTION_FAILED,
            JobStatus.COMMENT_FAILED,
            JobStatus.FAILED,
        }
    )
    _IN_FLIGHT_RETRY_STEP = {
        JobStatus.DOWNLOADREEL_RUNNING: "download",
        JobStatus.GEMINI_OPENING: "ai",
        JobStatus.GEMINI_GENERATING: "ai",
        JobStatus.AI_ANALYZING: "ai",
        JobStatus.CDHA_OPENING: "cdha",
        JobStatus.CDHA_UPLOADING: "cdha",
        JobStatus.CDHA_ANALYZING: "cdha",
        JobStatus.SCREENSHOTS_CAPTURING: "screenshots",
        JobStatus.FACEBOOK_PREPARING: "facebook_prepare",
        JobStatus.POST_URL_EXTRACTING: "facebook_permalink",
        JobStatus.COMMENT_ADDING: "facebook_comment",
    }

    def __init__(
        self,
        repository: JobRepositoryPort,
        stages: WorkflowStagePort,
        *,
        max_steps_per_run: int = 12,
        auto_approve_review: bool = False,
        require_facebook_confirmation: bool = True,
    ) -> None:
        self._repository = repository
        self._stages = stages
        self._max_steps = max(1, int(max_steps_per_run))
        self._auto_approve_review = bool(auto_approve_review)
        self._require_facebook_confirmation = bool(require_facebook_confirmation)

    async def execute(
        self, job_id: str, *, allow_facebook_publish: bool = False
    ) -> JobResult:
        effective_publish_permission = (
            allow_facebook_publish or not self._require_facebook_confirmation
        )
        for _ in range(self._max_steps):
            job = self._repository.get_job(job_id)
            if job is None:
                return JobResult.failure_result(job_id, f"Job not found: {job_id}")

            if job.status is JobStatus.COMPLETED:
                return self._success(job_id, job.status)
            if job.status in self._FAILURES:
                return JobResult.failure_result(
                    job_id, job.error_message or f"Workflow stopped at {job.status.value}"
                )
            if self._requires_manual_action(
                job.status, effective_publish_permission
            ):
                return self._success(job_id, job.status, manual=True)

            before = job.status
            result = await self._advance(
                job_id, job.status, effective_publish_permission
            )
            if not result.success:
                message = result.error or f"Stage failed from {before.value}"
                current = self._repository.get_job(job_id)
                if current is not None and current.status is before:
                    self._repository.transition(
                        job_id,
                        JobStatus.FAILED,
                        details={
                            "error_code": "STAGE_FAILED_WITHOUT_TRANSITION",
                            "error": message,
                            "stage": before.value,
                            "retryable": False,
                        },
                        event_type="PIPELINE_ERROR",
                    )
                return JobResult.failure_result(
                    job_id, message
                )

            refreshed = self._repository.get_job(job_id)
            if refreshed is None:
                return JobResult.failure_result(job_id, "Job disappeared during processing")
            if refreshed.status is before:
                message = f"Workflow made no progress from {before.value}"
                self._repository.transition(
                    job_id,
                    JobStatus.FAILED,
                    details={
                        "error_code": "WORKFLOW_NO_PROGRESS",
                        "error": message,
                        "stage": before.value,
                        "retryable": False,
                    },
                    event_type="PIPELINE_ERROR",
                )
                return JobResult.failure_result(
                    job_id, message
                )
            if self._requires_manual_action(
                refreshed.status, effective_publish_permission
            ):
                return self._success(job_id, refreshed.status, manual=True)

        current = self._repository.get_job(job_id)
        status = current.status.value if current else "UNKNOWN"
        return JobResult.failure_result(
            job_id, f"Workflow exceeded {self._max_steps} steps at {status}"
        )

    async def _advance(
        self, job_id: str, status: JobStatus, allow_facebook_publish: bool
    ) -> StageExecutionResult:
        if status in self._IN_FLIGHT_RETRY_STEP:
            retry_step = self._IN_FLIGHT_RETRY_STEP[status]
            self._repository.transition(
                job_id,
                JobStatus.RETRY_PENDING,
                details={"reason": "interrupted_stage_recovery"},
                data_patch={"retry_step": retry_step},
                event_type="JOB_RECOVERED",
            )
            return StageExecutionResult(True)

        if status is JobStatus.CREATED:
            return await self._stages.download(job_id)
        if status is JobStatus.DOWNLOADED:
            return await self._stages.analyze(job_id)
        if status is JobStatus.CLINICAL_FACTORS_GENERATED:
            return await self._stages.analyze_cdha(job_id)
        if status is JobStatus.CDHA_ANALYZED:
            return await self._stages.capture_screenshots(job_id)
        if status is JobStatus.SCREENSHOTS_CAPTURED:
            self._repository.transition(job_id, JobStatus.WAITING_FOR_REVIEW)
            return StageExecutionResult(True)
        if status is JobStatus.WAITING_FOR_REVIEW:
            if self._auto_approve_review:
                return await self._stages.approve_review(job_id)
            return StageExecutionResult(True, pending_manual_action="Review analysis")
        if status is JobStatus.APPROVED:
            return await self._stages.facebook(job_id)
        if status is JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW:
            if allow_facebook_publish:
                return await self._stages.facebook(job_id)
            return StageExecutionResult(True, pending_manual_action="Confirm Facebook publish")
        if status in {
            JobStatus.FACEBOOK_PUBLISHING,
            JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
            JobStatus.PUBLISH_RECONCILIATION_REQUIRED,
        }:
            return await self._stages.reconcile_facebook(job_id)
        if status is JobStatus.FACEBOOK_PUBLISHED:
            return await self._stages.extract_permalink(job_id)
        if status is JobStatus.POST_URL_EXTRACTED:
            return await self._stages.add_permalink_comment(job_id)
        if status is JobStatus.COMMENT_ADDED:
            self._repository.transition(job_id, JobStatus.COMPLETED)
            return StageExecutionResult(True)
        if status is JobStatus.RETRY_PENDING:
            job = self._repository.get_job(job_id)
            retry_step = str(job.data.get("retry_step", "download")) if job else "download"
            return await self._retry_stage(job_id, retry_step)
        return StageExecutionResult(False, error=f"Cannot process status {status.value}")

    def _requires_manual_action(
        self, status: JobStatus, allow_facebook_publish: bool
    ) -> bool:
        if status is JobStatus.WAITING_FOR_REVIEW:
            return not self._auto_approve_review
        if status is JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW:
            return not allow_facebook_publish
        return status in self._MANUAL_BOUNDARIES

    async def _retry_stage(self, job_id: str, retry_step: str) -> StageExecutionResult:
        if retry_step == "download":
            return await self._stages.download(job_id)
        if retry_step in {"ai", "gemini", "ai_analysis", "ai_analyzing"}:
            return await self._stages.analyze(job_id)
        if retry_step in {"cdha", "cdha_opening"}:
            return await self._stages.analyze_cdha(job_id)
        if retry_step == "screenshots":
            return await self._stages.capture_screenshots(job_id)
        if retry_step == "facebook_prepare":
            return await self._stages.facebook(job_id)
        if retry_step == "facebook_permalink":
            return await self._stages.extract_permalink(job_id)
        if retry_step == "facebook_comment":
            return await self._stages.add_permalink_comment(job_id)
        return StageExecutionResult(False, error=f"Unknown retry step: {retry_step}")

    @staticmethod
    def _success(job_id: str, status: JobStatus, *, manual: bool = False) -> JobResult:
        return JobResult.success_result(
            job_id,
            {
                "workflow_status": status.value,
                "pending_manual_action": manual,
            },
        )
