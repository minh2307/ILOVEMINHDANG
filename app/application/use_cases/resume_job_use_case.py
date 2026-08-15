from __future__ import annotations

from app.application.ports.job_repository_port import JobRepositoryPort
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.domain.enums.job_status import JobStatus
from app.domain.models.job_result import JobResult
from app.domain.policies.external_side_effect_policy import (
    LargeUploadApproval,
    large_upload_gate_required,
    large_upload_is_authorized,
    sha256_file,
    video_metadata,
)


class ResumeJobUseCase:
    """Resume from a persisted boundary without repeating verified side effects."""

    _FAILURES = frozenset(
        {
            JobStatus.DOWNLOADREEL_FAILED,
            JobStatus.GEMINI_FAILED,
            JobStatus.AI_FAILED,
            JobStatus.CDHA_FAILED,
            JobStatus.SCREENSHOTS_FAILED,
            JobStatus.FACEBOOK_PUBLISH_FAILED,
            JobStatus.POST_URL_EXTRACTION_FAILED,
            JobStatus.COMMENT_FAILED,
            JobStatus.FAILED,
        }
    )

    def __init__(
        self,
        repository: JobRepositoryPort,
        scheduler: ScheduleWorkflowJobsUseCase,
        *,
        cdha_large_file_threshold_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler
        self._cdha_large_file_threshold_bytes = max(
            1, int(cdha_large_file_threshold_bytes)
        )

    async def execute(
        self,
        job_id: str,
        *,
        large_upload_job_id: str | None = None,
        large_upload_sha256: str | None = None,
        large_upload_size_bytes: int | None = None,
        confirmation: str | None = None,
        dry_run: bool = False,
    ) -> JobResult:
        job = self._repository.get_job(job_id)
        if job is None:
            return JobResult.failure_result(job_id, f"Job not found: {job_id}")
        enforce_guard = getattr(
            self._repository, "enforce_facebook_submission_guard", None
        )
        if callable(enforce_guard):
            job = enforce_guard(job_id)
        if (
            job.status
            is JobStatus.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW
        ):
            return JobResult.success_result(
                job_id,
                {
                    "workflow_status": job.status.value,
                    "queued": False,
                    "pending_manual_action": True,
                },
            )
        if job.status is JobStatus.COMPLETED:
            return JobResult.success_result(
                job_id, {"workflow_status": job.status.value, "queued": False}
            )
        path, expected_size, expected_sha = video_metadata(job.data)
        is_large = expected_size > self._cdha_large_file_threshold_bytes
        approval_requested = any(
            value is not None
            for value in (
                large_upload_job_id,
                large_upload_sha256,
                large_upload_size_bytes,
                confirmation,
            )
        )
        if approval_requested:
            supplied_job = str(large_upload_job_id or "")
            supplied_sha = str(large_upload_sha256 or "").lower()
            supplied_size = int(large_upload_size_bytes or 0)
            expected_phrase = LargeUploadApproval.expected_phrase(
                supplied_job, supplied_sha, supplied_size
            )
            valid_scope = bool(
                is_large
                and supplied_job == job.job_id
                and supplied_sha == expected_sha
                and supplied_size == expected_size
                and confirmation == expected_phrase
                and path is not None
                and path.is_file()
                and path.stat().st_size == expected_size
                and sha256_file(path) == expected_sha
            )
            if not valid_scope:
                return JobResult.failure_result(
                    job_id,
                    "Large-upload approval does not match the full job ID, SHA-256, size, confirmation phrase, and local file",
                )
            approval = LargeUploadApproval.grant_data(
                job.job_id, expected_sha, expected_size
            )
            if dry_run:
                return JobResult.success_result(
                    job_id,
                    {
                        "workflow_status": job.status.value,
                        "queued": False,
                        "dry_run": True,
                        "approval_would_be_granted": approval,
                    },
                )
            self._repository.update_data(
                job_id, {LargeUploadApproval.DATA_KEY: approval}
            )
            job = self._repository.get_job(job_id) or job
        if (
            large_upload_gate_required(job, self._cdha_large_file_threshold_bytes)
            and not large_upload_is_authorized(
                job, self._cdha_large_file_threshold_bytes
            )
        ):
            return JobResult.failure_result(
                job_id,
                "Large CDHA upload is blocked pending a one-shot approval for the exact job ID, SHA-256, and size",
            )
        if job.status in self._FAILURES:
            return JobResult.failure_result(
                job_id,
                f"Job is in {job.status.value}; use the official retry command.",
            )
        manual_commands = {
            JobStatus.WAITING_FOR_REVIEW: "review",
            JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW: "confirm-publish",
            JobStatus.WAITING_FOR_AUTH_REVIEW: "complete authentication, then resume",
            JobStatus.REJECTED: "create-job",
            JobStatus.BLOCKED: "resolve the blocking event before retry",
            JobStatus.BLOCKED_USER_APPROVAL: "supply the scoped large-upload approval",
            JobStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED: "use read-only reconciliation or an audited publication decision",
            JobStatus.CANCELLED: "create-job",
        }
        if job.status in manual_commands:
            return JobResult.failure_result(
                job_id,
                f"Job requires manual action from {job.status.value}: "
                f"{manual_commands[job.status]}",
            )
        try:
            queued = await self._scheduler.schedule_job(job_id)
        except ValueError as exc:
            return JobResult.failure_result(job_id, str(exc))
        return JobResult.success_result(
            job_id,
            {
                "workflow_status": job.status.value,
                "queued": queued,
                "reconciliation_only": job.status in {
                    JobStatus.FACEBOOK_PUBLISHING,
                    JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
                    JobStatus.PUBLISH_RECONCILIATION_REQUIRED,
                },
            },
        )
