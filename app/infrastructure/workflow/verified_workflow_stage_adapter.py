from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.application.dto.stage_execution_result import StageExecutionResult
from app.models.results import PipelineResult
from app.workflows.cdha_pipeline import VerifiedWorkflowStages


class VerifiedWorkflowStageAdapter:
    """Adapts the verified browser pipeline to one external action per call."""

    def __init__(self, pipeline: VerifiedWorkflowStages) -> None:
        if pipeline.auto_continue or pipeline.interactive_review:
            raise ValueError(
                "VerifiedWorkflowStageAdapter requires auto_continue=False and "
                "interactive_review=False"
            )
        self._pipeline = pipeline

    @staticmethod
    async def _run(
        operation: Callable[[str], Awaitable[PipelineResult]], job_id: str
    ) -> StageExecutionResult:
        result = await operation(job_id)
        return StageExecutionResult(
            success=result.success,
            error=result.error,
            pending_manual_action=result.pending_manual_action,
            data={"current_status": result.current_status},
        )

    async def download(self, job_id: str) -> StageExecutionResult:
        return await self._run(self._pipeline.execute_download_stage, job_id)

    async def analyze(self, job_id: str) -> StageExecutionResult:
        return await self._run(self._pipeline.execute_ai_stage, job_id)

    async def analyze_cdha(self, job_id: str) -> StageExecutionResult:
        return await self._run(self._pipeline.execute_cdha_stage, job_id)

    async def capture_screenshots(self, job_id: str) -> StageExecutionResult:
        return await self._run(self._pipeline.execute_screenshot_stage, job_id)

    async def approve_review(self, job_id: str) -> StageExecutionResult:
        return await self._run(self._pipeline.execute_review_stage, job_id)

    async def facebook(self, job_id: str) -> StageExecutionResult:
        return await self._run(self._pipeline.execute_facebook_stage, job_id)

    async def reconcile_facebook(self, job_id: str) -> StageExecutionResult:
        return await self._run(
            self._pipeline.execute_facebook_reconciliation_stage, job_id
        )

    async def extract_permalink(self, job_id: str) -> StageExecutionResult:
        return await self._run(self._pipeline.execute_permalink_stage, job_id)

    async def add_permalink_comment(self, job_id: str) -> StageExecutionResult:
        return await self._run(self._pipeline.execute_comment_stage, job_id)
