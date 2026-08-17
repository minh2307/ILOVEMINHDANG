from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from app.domain.enums.job_status import JobStatus
from app.dashboard.schemas import DashboardJob

class OperationPolicyResponse(BaseModel):
    allowed: bool
    operation: str
    reason: Optional[str] = None
    risk: str = "LOW"

class OperationsPolicy:
    """Centralized safety policy for dashboard operations."""
    
    _FAILURES = frozenset({
        JobStatus.DOWNLOADREEL_FAILED,
        JobStatus.GEMINI_FAILED,
        JobStatus.AI_FAILED,
        JobStatus.CDHA_FAILED,
        JobStatus.SCREENSHOTS_FAILED,
        JobStatus.FACEBOOK_PUBLISH_FAILED,
        JobStatus.POST_URL_EXTRACTION_FAILED,
        JobStatus.COMMENT_FAILED,
        JobStatus.FAILED,
        JobStatus.NEEDS_CDHA_LOGIN,
    })

    _UNCERTAIN_PUBLISH = frozenset({
        JobStatus.FACEBOOK_PUBLISHING,
        JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
        JobStatus.PUBLISH_RECONCILIATION_REQUIRED,
        JobStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED,
        JobStatus.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW
    })
    
    _COMPLETED = frozenset({
        JobStatus.COMPLETED,
        JobStatus.CANCELLED
    })

    def get_allowed_operations(self, job: DashboardJob) -> List[OperationPolicyResponse]:
        status_value = job.status
        try:
            status = JobStatus(status_value)
        except ValueError:
            status = None

        return [
            self.evaluate_retry(status, job),
            self.evaluate_resume(status, job),
            self.evaluate_reconcile(status, job),
            self.evaluate_cancel(status, job),
            self.evaluate_mark_manual_review(status, job)
        ]

    def evaluate_retry(self, status: JobStatus | None, job: DashboardJob) -> OperationPolicyResponse:
        if not status:
            return OperationPolicyResponse(allowed=False, operation="RETRY", reason="Unknown status", risk="BLOCKED")
        
        if status in self._UNCERTAIN_PUBLISH:
            return OperationPolicyResponse(
                allowed=False, 
                operation="RETRY", 
                reason="External side effect is unconfirmed. Use Reconcile instead.", 
                risk="HIGH_RISK"
            )
            
        if status in self._COMPLETED:
            return OperationPolicyResponse(
                allowed=False,
                operation="RETRY",
                reason="Job is already completed or cancelled.",
                risk="BLOCKED"
            )

        if status not in self._FAILURES:
            return OperationPolicyResponse(
                allowed=False,
                operation="RETRY",
                reason=f"Job is not in a retryable failure state (current: {status.value})",
                risk="BLOCKED"
            )

        if job.attempt >= job.max_attempts:
            return OperationPolicyResponse(
                allowed=False,
                operation="RETRY",
                reason=f"Max attempts reached ({job.attempt}/{job.max_attempts})",
                risk="BLOCKED"
            )

        risk = "LOW_RISK"
        if status == JobStatus.CDHA_FAILED or status == JobStatus.SCREENSHOTS_FAILED:
            risk = "MEDIUM_RISK"
        elif status == JobStatus.FACEBOOK_PUBLISH_FAILED:
            risk = "HIGH_RISK"

        return OperationPolicyResponse(allowed=True, operation="RETRY", risk=risk)

    def evaluate_resume(self, status: JobStatus | None, job: DashboardJob) -> OperationPolicyResponse:
        if not status:
            return OperationPolicyResponse(allowed=False, operation="RESUME", reason="Unknown status", risk="BLOCKED")

        if status in self._COMPLETED:
            return OperationPolicyResponse(
                allowed=False,
                operation="RESUME",
                reason="Job is already completed or cancelled.",
                risk="BLOCKED"
            )

        if status in self._FAILURES:
            return OperationPolicyResponse(
                allowed=False,
                operation="RESUME",
                reason="Job has failed. Use Retry instead.",
                risk="BLOCKED"
            )

        if status in self._UNCERTAIN_PUBLISH:
            return OperationPolicyResponse(
                allowed=False,
                operation="RESUME",
                reason="External side effect is unconfirmed. Use Reconcile instead.",
                risk="BLOCKED"
            )

        manual_commands = {
            JobStatus.WAITING_FOR_REVIEW,
            JobStatus.FACEBOOK_WAITING_FOR_MANUAL_REVIEW,
            JobStatus.WAITING_FOR_AUTH_REVIEW,
            JobStatus.REJECTED,
            JobStatus.BLOCKED,
            JobStatus.BLOCKED_USER_APPROVAL
        }
        if status in manual_commands:
            return OperationPolicyResponse(
                allowed=False,
                operation="RESUME",
                reason="Job requires manual action or approval.",
                risk="BLOCKED"
            )

        return OperationPolicyResponse(allowed=True, operation="RESUME", risk="LOW_RISK")

    def evaluate_reconcile(self, status: JobStatus | None, job: DashboardJob) -> OperationPolicyResponse:
        if not status:
            return OperationPolicyResponse(allowed=False, operation="RECONCILE", reason="Unknown status", risk="BLOCKED")

        if status in {
            JobStatus.FACEBOOK_PUBLISHING,
            JobStatus.PUBLISH_RECONCILIATION_REQUIRED,
            JobStatus.FACEBOOK_PUBLISH_UNCERTAIN,
            JobStatus.FACEBOOK_SUBMITTED_UNCONFIRMED_EXHAUSTED
        }:
            return OperationPolicyResponse(allowed=True, operation="RECONCILE", risk="LOW_RISK")
            
        return OperationPolicyResponse(
            allowed=False, 
            operation="RECONCILE", 
            reason="Reconciliation is only valid for uncertain Facebook publish states.", 
            risk="BLOCKED"
        )

    def evaluate_cancel(self, status: JobStatus | None, job: DashboardJob) -> OperationPolicyResponse:
        if not status:
            return OperationPolicyResponse(allowed=False, operation="CANCEL", reason="Unknown status", risk="BLOCKED")
            
        if status == JobStatus.CANCELLED:
            return OperationPolicyResponse(
                allowed=False, 
                operation="CANCEL", 
                reason="Job is already cancelled.", 
                risk="BLOCKED"
            )

        if status == JobStatus.COMPLETED:
            return OperationPolicyResponse(
                allowed=False, 
                operation="CANCEL", 
                reason="Job is already completed.", 
                risk="BLOCKED"
            )
            
        if status in self._UNCERTAIN_PUBLISH:
            return OperationPolicyResponse(
                allowed=False, 
                operation="CANCEL", 
                reason="External side effect is unconfirmed. Reconcile first before cancelling.", 
                risk="BLOCKED"
            )

        risk = "MEDIUM_RISK" if status == JobStatus.RUNNING else "LOW_RISK"
        return OperationPolicyResponse(allowed=True, operation="CANCEL", risk=risk)

    def evaluate_mark_manual_review(self, status: JobStatus | None, job: DashboardJob) -> OperationPolicyResponse:
        if not status:
            return OperationPolicyResponse(allowed=False, operation="MARK_MANUAL_REVIEW", reason="Unknown status", risk="BLOCKED")
            
        if status in self._COMPLETED:
            return OperationPolicyResponse(
                allowed=False,
                operation="MARK_MANUAL_REVIEW",
                reason="Job is already completed or cancelled.",
                risk="BLOCKED"
            )
            
        return OperationPolicyResponse(allowed=True, operation="MARK_MANUAL_REVIEW", risk="LOW_RISK")
