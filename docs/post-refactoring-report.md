# Post-refactoring report

## 1. Final architecture summary

The official dependency path is:

```text
root CLI / delegates
  → app.main (interface routing)
  → app.bootstrap (composition)
  → application use cases + ports
  → domain models/rules/errors
  ← infrastructure implementations injected at bootstrap
```

`ProcessJobUseCase` is the single state-aware workflow roadmap.
`ScheduleWorkflowJobsUseCase` only schedules. `FacebookBrowserWorker` only
claims one queue item, maintains its lease/locks, dispatches it, and persists the
result. SQLite and Playwright remain infrastructure details.

See [`architecture.md`](architecture.md) for the full component map.

## 2. Final relevant tree

```text
main.py
app/
├── bootstrap.py
├── main.py
├── domain/{enums,models,exceptions,rules}/
├── application/{dto,ports,services,use_cases}/
├── infrastructure/{browser,persistence,workflow,legacy}/
├── adapters/
├── ai/
├── browser/
├── config/
├── services/
└── workflows/
workers/{main.py,facebook_browser_worker.py}
scripts/{run_worker.sh,run_orchestrator.sh,migrate_legacy_queue.py,...}
tests/{unit,fixtures,test_*.py}
docs/{architecture,workflow,folder-structure,migration-notes,operations}.md
runtime/  # ignored, untouched
data/     # ignored, additive SQLite migration
```

See [`folder-structure.md`](folder-structure.md) for responsibilities.

## 3. Migration map

The complete table is in [`migration-notes.md`](migration-notes.md). Key moves:

| Old authority | New authority | Action |
| --- | --- | --- |
| workflow enum/model/state machine | `app/domain/` | Merged; old names re-export |
| repository wrapper/stub | infrastructure SQLite repository | Replaced with full additive implementation |
| two official database paths | `DATABASE_PATH` | Consolidated tables |
| full-roadmap `CDHAPipeline` | `ProcessJobUseCase` | Wrapped as one-stage mechanics |
| hybrid worker/orchestrator script | official scheduler + worker | Deprecated delegate |
| duplicate browser worker/store | `infrastructure/legacy/facebook_browser` | Isolated + facade |
| click-only integrations/alternate Chrome launcher | `infrastructure/legacy/` | Isolated |
| pending old queue rows | `migrate_legacy_queue.py` | Read-only dry-run; safe apply mappings |

## 4. Unified workflow

```text
create/deduplicate → schedule → atomic claim → download → analyze → CDHA
→ WAITING_FOR_REVIEW → approve → prepare Facebook
→ FACEBOOK_WAITING_FOR_MANUAL_REVIEW → explicit publish confirmation
→ verified publish/permalink → exact permalink comment → COMPLETED
```

Every external boundary persists its result before the next operation. Detailed
state transitions, required data, retry and recovery behavior are documented in
[`workflow.md`](workflow.md).

## 5. Supported entry points

| Command | Purpose |
| --- | --- |
| `python main.py create-job --url URL` | Normalize, deduplicate, persist, queue |
| `python main.py orchestrator [--once]` | Schedule eligible states only |
| `python main.py worker [--once]` | Claim and process queue work |
| `python main.py status --job-id ID` | Inspect job, events and queue rows |
| `python main.py resume --job-id ID` | Schedule one eligible state |
| `python main.py retry --job-id ID` | Persist retry stage and schedule |
| `python main.py review --job-id ID` | Medical review gate |
| `python main.py confirm-publish --job-id ID` | Exact-phrase Facebook gate |
| `python main.py worker --preflight-only` | Validate without claim/browser/publish |

Legacy flags and scripts remain as compatibility paths and are not used in new
documentation or composition.

## 6. State machine

There is one `JobStatus` enum and one `JobStateTransitions` map. It covers the
download, AI/Gemini, CDHA, screenshots, review, Facebook, permalink/comment,
retry, blocked, terminal, and queue-operation states. Invalid transitions raise
`InvalidTransitionError`. The exhaustive current→next table is in
[`workflow.md`](workflow.md#state-machine).

## 7. Files changed

Created:

- Authoritative domain enums/models/rules/errors and package boundaries.
- Application DTO/ports and process/create/retry/scheduler/queued use cases.
- Verified workflow-stage adapter, bootstrap, README, final docs, migration tool.
- Workflow, scheduler, queue lease/claim, migration, artifact, settings, and
  failure-persistence tests.

Modified:

- Typed settings/browser config, SQLite repository/queue, worker lifecycle,
  centralized browser manager, characterized pipeline, Facebook adapter, CLI,
  scripts, ignore/example environment files, and Vietnamese run guide.

Moved/isolated:

- Repository implementation into infrastructure.
- Error taxonomy into domain.
- Old browser job store/worker, click-only integrations, alternate Chrome
  launcher, and imported mini-projects under `app/infrastructure/legacy/`.

Deprecated:

- `workers.main`, old job/orchestrator scripts, compatibility repository/model/
  state-machine/container imports, and old flag-style CLI surface.

Deleted:

- No runtime data, database row, profile, download, screenshot, log, or secret.
- Superseded source locations appear deleted only because their content was
  moved or replaced by compatibility facades.

## 8. Compatibility notes

The verified downloader, Ollama analysis, CDHA client, review behavior,
Facebook preparation/manual gate/reconciliation/permalink/comment logic, selector
fallbacks, privacy masking, and old CLI flags remain. Compatibility imports keep
existing tests and callers operational. Schema migration is additive. The old
queue file remains untouched and can be inspected before migration.

External behavior intentionally changed where it was unsafe: fake join/share/
metadata success is disabled; implicit auto-publish is removed; broad browser
`pkill` scripts now use exact manager-owned PID shutdown; live queue claims are
not recovered until their lease expires.

## 9. Test results

Final command:

```bash
.venv/bin/python -m compileall -q app workers config scripts
.venv/bin/pytest -q
git diff --check
```

Latest verification: **332 passed in 7.25s**; compilation, shell syntax, and whitespace checks
passed. Baseline before structural work was 265 passing tests. New coverage
includes the full fake workflow and manual gates, lifecycle use cases, atomic
claim, owner heartbeat, live/expired lease recovery, resource cleanup, additive
metadata/artifact persistence, exact permalink comment routing, and read-only
legacy migration.

## 10. Remaining risks

- Live Facebook/CDHA UI selectors and authentication states can change outside
  the repository. A controlled manual acceptance run is still required.
- Real publish/permalink verification is deliberately not automated in tests;
  doing so would create external content.
- Characterized browser clients and `CDHAPipeline` remain large files. They are
  behind application ports now; splitting them further is maintainability work,
  not an execution-path gap.
- Old `CREATE_POST`/unsupported legacy queue rows require manual mapping by
  design; migration never treats them as publish consent.

## 11. Verification checklist

Exact safe steps for creation, queue/worker operation, locking, download,
analysis, CDHA, both manual gates, crash resume, retry, and duplicate prevention
are in [`operations.md`](operations.md#manual-verification-checklist).
