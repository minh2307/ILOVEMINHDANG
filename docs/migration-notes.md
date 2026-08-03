# Migration notes

No database, profile, download, screenshot, log, cookie, or other runtime file
was deleted. The migration is additive and compatibility-first.

| Old file or module | New location / authority | Action | Reason |
| --- | --- | --- | --- |
| `app.models.workflow` | `app.domain.enums.job_status`, `app.domain.models.job`, `job_event` | Compatibility exports | One active job model and status enum |
| `app.workflows.state_machine` | `app.domain.rules.state_transitions` | Compatibility exports | One validated transition map |
| `app.errors` | `app.domain.exceptions.errors` | Compatibility facade | Domain owns the error taxonomy |
| `app.repositories.job_repository` | `app.infrastructure.persistence.sqlite_job_repository` | Compatibility facade | SQLite is an infrastructure detail |
| `app.infrastructure.persistence.sqlite_job_repository` stub | Same path, real repository | Replaced | Removed `get_job() -> None` and repository workflow decisions |
| `runtime/queue.db` official use | `DATABASE_PATH` queue tables | Replaced in composition | One persistent database and transaction policy |
| `CDHAPipeline` full-roadmap mode | `ProcessJobUseCase` + `VerifiedWorkflowStageAdapter` | Wrapped | Preserve verified mechanics while centralizing decisions |
| Fake Playwright post/group success | Official verified Facebook adapter | Disabled | Never claim unverified join/share/publish success |
| `config.dependency_container` | `app.bootstrap` | Compatibility facade | One explicit composition root |
| `workers.main` | `main.py worker` | Delegates | One CLI surface |
| `scripts/add_job.py` | `main.py create-job` | Deprecated delegate | No direct queue creation |
| `scripts/run_end_to_end_worker.py` | `main.py create-job` | Deprecated delegate | Removed hybrid two-database workflow |
| pending `runtime/queue.db` rows | `scripts/migrate_legacy_queue.py` | Dry-run/apply migration | Preserve old DB; map safe downloads, never auto-confirm publish |
| `scripts/run_worker.sh` | `main.py worker` | Delegates | One worker implementation |
| `scripts/run_orchestrator.sh` | `main.py orchestrator` / `create-job` | Delegates | Scheduler performs no browser work |
| browser start/check/stop shell logic | `facebook_browser_cli` / manager | Delegates | Exact managed PID; no broad `pkill` |
| `app.browser.facebook_job` | `app.infrastructure.legacy.facebook_browser.job_store` | Isolated + facade | Preserve characterization tests; not official queue |
| `app.browser.facebook_browser_worker` | `app.infrastructure.legacy.facebook_browser.worker` | Isolated + facade | Preserve old tests; not production dispatch |
| `app.integrations.facebook` click-only stack | `app.infrastructure.legacy.facebook_integrations` | Isolated | Unverified click flows are not part of official composition |
| alternate `ChromeProcessManager` | `app.infrastructure.legacy.chrome_process_manager` | Isolated | Browser lifecycle has one official manager and profile lock |
| `AutoFacebook-SonMinhShare/` | `app/infrastructure/legacy/AutoFacebook-SonMinhShare/` | Retained legacy | Historical crawler behavior remains available |
| `dowloadReelFB/` | `app/infrastructure/legacy/dowloadReelFB/` | Wrapped legacy | Verified downloader behavior retained behind adapter |

The old flag-style commands in `app.main` remain temporarily for operator
compatibility. New automation and documentation use subcommands. Removing those
flags is a later breaking-release decision, not part of this data-safe migration.

Run migration in dry-run mode first:

```bash
.venv/bin/python scripts/migrate_legacy_queue.py --source runtime/queue.db
```

Add `--apply` only after reviewing every item. The source database is opened
read-only and is never deleted or updated.
