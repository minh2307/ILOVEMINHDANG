from __future__ import annotations

from app.application.services.facebook_job_dispatcher import FacebookJobDispatcher
from app.application.use_cases.cancel_job_use_case import CancelJobUseCase
from app.application.use_cases.confirm_publish_use_case import ConfirmPublishUseCase
from app.application.use_cases.get_job_status_use_case import GetJobStatusUseCase
from app.application.use_cases.inspect_runtime_use_cases import (
    InspectBrowserUseCase,
    InspectQueueUseCase,
)
from app.application.use_cases.process_job_use_case import ProcessJobUseCase
from app.application.use_cases.process_queued_job_use_case import ProcessQueuedJobUseCase
from app.application.use_cases.reconcile_publish_use_case import ReconcilePublishUseCase
from app.application.use_cases.resume_job_use_case import ResumeJobUseCase
from app.application.use_cases.review_job_use_case import ReviewJobUseCase
from app.application.use_cases.create_job_use_case import CreateJobUseCase
from app.application.use_cases.retry_job_use_case import RetryJobUseCase
from app.application.use_cases.schedule_workflow_jobs_use_case import (
    ScheduleWorkflowJobsUseCase,
)
from app.browser.facebook_browser_manager import FacebookBrowserManager
from app.config.facebook_browser import FacebookBrowserConfig
from app.domain.enums.job_type import JobType
from app.infrastructure.browser.file_browser_lock import FileBrowserLock
from app.infrastructure.persistence.sqlite_job_queue import SQLiteJobQueue
from app.infrastructure.workflow.verified_workflow_stage_adapter import (
    VerifiedWorkflowStageAdapter,
)
from app.infrastructure.persistence.sqlite_job_repository import JobRepository
from app.services.review_service import ReviewService
from app.workflows.cdha_pipeline import VerifiedWorkflowStages
from workers.facebook_browser_worker import FacebookBrowserWorker


class DependencyContainer:
    """Official composition root for worker and orchestrator processes."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.browser_config = FacebookBrowserConfig.from_settings(settings)
        self.browser_config.ensure_directories()

        self.browser_lock = FileBrowserLock(
            str(self.browser_config.lock_path),
            process_name="cdha-worker",
            browser_profile=str(self.browser_config.profile_path),
            browser_port=self.browser_config.cdp_port,
            timeout_seconds=self.settings.browser_lock_timeout_seconds,
            heartbeat_seconds=self.settings.browser_lock_heartbeat_seconds,
        )
        self.browser_manager = FacebookBrowserManager(
            settings=self.settings,
            config=self.browser_config,
            browser_lock=self.browser_lock,
        )

        # Workflow jobs and queue work items intentionally share one SQLite
        # database file while retaining separate tables and event histories.
        self.job_repository = JobRepository(self.settings.database_path)
        self.job_repository.initialize()
        self.job_queue = SQLiteJobQueue(str(self.settings.database_path))

        self.pipeline = VerifiedWorkflowStages(
            self.settings,
            self.job_repository,
            chrome=self.browser_manager,
            auto_continue=False,
            interactive_review=False,
            # This provider is reachable only from an explicitly confirmed
            # PROCESS_WORKFLOW queue item created by the CLI manual gate.
            confirmation_provider=lambda _prompt: "1",
        )
        self.stage_adapter = VerifiedWorkflowStageAdapter(self.pipeline)
        self.process_job = ProcessJobUseCase(
            self.job_repository, self.stage_adapter
        )
        self.scheduler = ScheduleWorkflowJobsUseCase(
            self.job_repository, self.job_queue
        )
        self.create_job = CreateJobUseCase(self.job_repository, self.scheduler)
        self.retry_job = RetryJobUseCase(self.job_repository, self.scheduler)
        self.process_queued_job = ProcessQueuedJobUseCase(
            self.process_job,
            self.job_repository,
            self.retry_job,
        )
        self.cancel_job = CancelJobUseCase(self.job_repository)
        self.resume_job = ResumeJobUseCase(self.job_repository, self.scheduler)
        self.confirm_publish = ConfirmPublishUseCase(
            self.job_repository, self.scheduler
        )
        self.get_job_status = GetJobStatusUseCase(
            self.job_repository, self.job_queue
        )
        self.inspect_browser = InspectBrowserUseCase(
            self.browser_manager, self.browser_lock
        )
        self.inspect_queue = InspectQueueUseCase(self.job_queue)
        self.review_job = ReviewJobUseCase(
            self.job_repository, self.scheduler, ReviewService(settings, self.job_repository).review
        )

        self.dispatcher = FacebookJobDispatcher(
            {JobType.PROCESS_WORKFLOW: self.process_queued_job}
        )

        def current_workflow_stage(queue_job) -> str:
            workflow_job_id = str(
                queue_job.payload.get("workflow_job_id") or queue_job.job_id
            )
            workflow_job = self.job_repository.get_job(workflow_job_id)
            return workflow_job.status.value if workflow_job else "RUNNING"

        self.worker = FacebookBrowserWorker(
            queue=self.job_queue,
            browser_lock=self.browser_lock,
            dispatcher=self.dispatcher,
            lock_wait_timeout_seconds=self.settings.browser_lock_wait_timeout_seconds,
            lock_retry_interval_seconds=self.settings.browser_lock_retry_interval_seconds,
            retry_base_seconds=5,
            retry_max_seconds=40,
            retry_jitter_seconds=self.settings.retry_jitter_seconds,
            queue_lease_seconds=self.settings.job_lease_seconds,
            queue_heartbeat_seconds=self.settings.job_heartbeat_seconds,
            poll_interval_seconds=self.settings.worker_poll_interval_seconds,
            stage_timeout_seconds=self.settings.worker_stage_timeout_seconds,
            close_resources=self.browser_manager.close,
            stage_provider=current_workflow_stage,
            startup_diagnostics={
                **self.settings.sanitized_runtime_configuration(),
                "configuration_fingerprint": self.settings.configuration_fingerprint(),
            },
        )


def build_container(settings) -> DependencyContainer:
    return DependencyContainer(settings)
