from __future__ import annotations

from datetime import UTC, datetime

from app.application.ports.job_repository_port import JobRepositoryPort
from app.browser.facebook_client import FacebookWebClient
from app.domain.enums.job_status import JobStatus
from app.domain.models.job_result import JobResult


class ResolvePublicationDecisionUseCase:
    """Record an audited operator decision without invoking Facebook UI."""

    DECISIONS = frozenset({"attach-permalink", "mark-unverified", "confirm-duplicate"})

    def __init__(self, repository: JobRepositoryPort) -> None:
        self._repository = repository

    @staticmethod
    def expected_phrase(job_id: str, decision: str) -> str:
        return f"RESOLVE-PUBLICATION:{job_id}:{decision}"

    async def execute(
        self,
        job_id: str,
        *,
        decision: str,
        confirmation: str,
        permalink: str | None = None,
        requested_by: str = "operator",
    ) -> JobResult:
        job = self._repository.get_job(job_id)
        if job is None:
            return JobResult.failure_result(job_id, f"Job not found: {job_id}")
        if job.status is not JobStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED:
            return JobResult.failure_result(
                job_id, f"Publication decision is not allowed from {job.status.value}"
            )
        if decision not in self.DECISIONS:
            return JobResult.failure_result(job_id, "Unknown publication decision")
        if confirmation != self.expected_phrase(job_id, decision):
            return JobResult.failure_result(job_id, "Publication decision confirmation did not match")
        decided_at = datetime.now(UTC).isoformat()
        audit = {
            "decision": decision,
            "requested_by": requested_by,
            "decided_at": decided_at,
        }
        if decision == "attach-permalink":
            if not permalink:
                return JobResult.failure_result(job_id, "attach-permalink requires --permalink")
            try:
                canonical = FacebookWebClient.normalize_permalink(
                    permalink, base_url=permalink
                )
            except ValueError as exc:
                return JobResult.failure_result(job_id, str(exc))
            post_id = FacebookWebClient.extract_post_id(canonical)
            updated = self._repository.transition(
                job_id,
                JobStatus.FACEBOOK_PUBLISHED,
                event_type="OPERATOR_PUBLICATION_DECISION",
                details=audit,
                data_patch={
                    "facebook_post_url": canonical,
                    "facebook_post_id": post_id or job.data.get("facebook_post_id"),
                    "facebook_publication_verified": True,
                    "facebook_submission_status": "OPERATOR_VERIFIED",
                    "facebook_publication_state": "PUBLISHED_CONFIRMED",
                    "facebook_operator_decision": audit,
                },
            )
        else:
            updated = self._repository.transition(
                job_id,
                JobStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED,
                event_type="OPERATOR_PUBLICATION_DECISION",
                details=audit,
                data_patch={"facebook_operator_decision": audit},
            )
        return JobResult.success_result(
            job_id,
            {
                "workflow_status": updated.status.value,
                "decision": decision,
                "external_action_performed": False,
            },
        )
