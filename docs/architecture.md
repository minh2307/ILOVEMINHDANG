# Architecture

## Dependency direction

```text
main.py / workers.main / compatibility scripts
                    │
                    ▼
        app.main + app.bootstrap
                    │
                    ▼
          application use cases
        ┌───────────┴───────────┐
        ▼                       ▼
 domain models/rules     application ports
                                ▲
                                │ implements
                         infrastructure
```

The domain contains the authoritative `JobStatus`, `JobType`, `Job`, `JobEvent`,
transition rules, and error taxonomy. It has no browser, SQLite, environment,
HTTP, or filesystem imports. Application code depends on domain types and
protocols. Infrastructure implements those protocols. `app.bootstrap` is the
only official composition root that selects concrete adapters.

## Authoritative components

| Responsibility | Implementation |
| --- | --- |
| Root CLI | `main.py` → `app.main.main` |
| Composition | `app.bootstrap.DependencyContainer` |
| Workflow decisions | `app.application.use_cases.process_job_use_case.ProcessJobUseCase` |
| Scheduling | `ScheduleWorkflowJobsUseCase` |
| Worker | `workers.facebook_browser_worker.FacebookBrowserWorker` |
| Workflow persistence | `app.infrastructure.persistence.sqlite_job_repository.JobRepository` |
| Durable queue | `app.infrastructure.persistence.sqlite_job_queue.SQLiteJobQueue` |
| Browser lifecycle | `app.browser.facebook_browser_manager.FacebookBrowserManager` |
| Browser ownership | `app.infrastructure.browser.file_browser_lock.FileBrowserLock` |
| Verified external stages | `VerifiedWorkflowStageAdapter` over the characterized pipeline/adapters |
| Status rules | `app.domain.rules.state_transitions.JobStateTransitions` |

`CDHAPipeline` is retained as the characterized implementation of external
stage mechanics. In the official path it is configured with
`auto_continue=False` and `interactive_review=False`; it performs one external
stage per call. It no longer determines the end-to-end roadmap. The application
use case reads persisted state, selects the next port operation, enforces manual
boundaries, and detects no-progress/infinite-step conditions.

## Persistence

One SQLite file (`DATABASE_PATH`) contains separate `jobs`, `job_events`,
`queue`, and `queue_events` tables. Schema initialization is additive: existing
tables receive missing columns through `ALTER TABLE`; rows and runtime files are
not deleted.

The `jobs` table explicitly stores job type, current/previous status, input and
output payloads, artifact paths, error fields, attempts, claim metadata, and
timestamps. `data_json` remains during migration so verified legacy adapters
retain their artifact keys.

Queue claims use `BEGIN IMMEDIATE` and a conditional update. A claim records
`claimed_by`, `lease_expires_at`, and `last_heartbeat`. Only the owner can renew
the lease. Startup recovery changes only expired or pre-lease legacy claims to
`RETRYABLE` and increments attempts. Retry stops at `max_attempts` and becomes
`BLOCKED`.

## Browser safety

The integrated workflow uses Playwright over one managed Chrome CDP endpoint and
one persistent profile: `runtime/chrome_profiles/cdha_automation`. The central manager owns startup, exact-PID shutdown, context/page creation,
timeouts, manual-login checkpoints, and redacted diagnostics. Browser CLI and
Worker share the same `FileBrowserLock`, whose metadata records the absolute
canonical profile, PID identity, port, job, heartbeat, and owner token.

No official adapter launches an independent browser. The disconnected
standalone post/group adapters are disabled because they could not verify their
results; the former implicit auto-publish path was removed.

## Compatibility boundaries

Compatibility modules (`app.models.workflow`, `app.workflows.state_machine`,
`app.repositories.job_repository`, `config.dependency_container`, and old
browser job imports) re-export authoritative implementations or explicitly
isolated legacy behavior. They introduce no second production composition.

The pre-refactor evidence is preserved in
[`pre-refactoring-report.md`](pre-refactoring-report.md).
