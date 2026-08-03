# Pre-refactoring Report

Date: 2026-07-29

This report records the repository state before structural refactoring. The worktree already contained a large uncommitted migration when this audit began. Those changes and all runtime data are treated as protected user work.

## Safety baseline

- Root entrypoint: `main.py` delegates to `app.main.main`.
- Python compilation: pass for `app`, `workers`, `config`, and `scripts`.
- Pytest collection: 265 tests.
- Initial test run: 260 passed, 5 failed.
- Two pre-existing contract regressions were root-caused and minimally repaired before structural work: Facebook page-state navigation detection and Ollama Clinical Factors newline formatting.
- Clean baseline after those repairs: **265 passed in 6.36s**.
- `.env` exists locally and was not read. Runtime contains real profiles, cookies, videos, queue data, locks, and process metadata; none was deleted or rewritten.
- Static analysis found no direct Python import cycle. It did find parallel architectures and inverted layer dependencies.

## 1. Current execution paths

| Entry Point | Purpose | Modules Called | Still Required? | Target Replacement |
|---|---|---|---|---|
| `python main.py ...` | Official flag-based workflow CLI | `app.main`, legacy repository, `CDHAPipeline`, browser clients/adapters | Yes | Keep root file; replace flags with one subcommand CLI while retaining compatibility aliases |
| `python -m app.main ...` | Direct access to the same CLI implementation | Concrete settings/repository/browser/workflow modules | Internal only | `app/interfaces/cli/commands.py` invoked by root `main.py` |
| `python -m workers.main` | Durable Facebook worker | `DependencyContainer`, SQLite queue, browser lock, Facebook dispatcher | Yes | `python main.py worker` |
| `scripts/run_worker.sh` | Environment-checking worker launcher | `.venv`, `workers.main` | Yes as wrapper | Delegate to `python main.py worker` |
| `scripts/run_facebook_worker.sh` | Alias for `run_worker.sh` | `scripts/run_worker.sh` | Compatibility only | Deprecation notice, then delegate |
| `scripts/run_orchestrator.sh URL` | Launch hybrid orchestrator | `scripts/run_end_to_end_worker.py` | Behavior required | `python main.py create-job --url ...` plus `python main.py orchestrator` |
| `scripts/run_end_to_end_worker.py URL` | Queues download/post but directly runs AI/CDHA/review | Subclass of `CDHAPipeline`, queue, repository, Chrome | Replace | Application orchestrator that schedules steps only |
| `scripts/add_job.py URL` | Add download job to hard-coded queue path | `SQLiteJobQueue`, clean `FacebookJob` | Replace | `python main.py create-job --url ...` |
| `python -m app.browser.facebook_browser_cli` | Start/check/stop Chrome or run duplicate worker | Duplicate browser worker/store/integrations | No active public path needed | `python main.py browser ...` and official worker |
| `scripts/start_facebook_browser.sh` | Start Chrome CDP directly | Shell `google-chrome` | No | Central browser manager command |
| `scripts/check_facebook_browser.sh` | Probe CDP port | Shell `curl` | Optional compatibility | Central browser status command |
| `scripts/stop_facebook_browser.sh` | Stop PID then broad `pkill` fallback | Shell process control | No | Scoped manager shutdown using verified PID/profile |
| `inject_cookie_script.py` | Inject Gemini/CDHA cookie-export files | Browser manager and shared context | Optional admin tool | Manual login checkpoint; retain only as explicit deprecated migration utility if necessary |
| `retake_screenshots.py [JOB_ID]` | Re-capture CDHA screenshots | Legacy repository, Chrome, screenshot service | Useful maintenance action | `python main.py retake-screenshots --job-id ...`; remove hard-coded fallback job ID |
| `legacy/.../fb_downloader.py` | Interactive clipboard/manual downloader | Legacy downloader modules and its browser manager | Working behavior required through adapter | Official download use case; standalone emits deprecation notice |
| `legacy/.../visit-like-post.py` | Compatibility producer for old Facebook modes | Duplicate `FacebookJobClient` stack | Legacy behavior not yet preserved by new adapters | Characterize and wrap real actions; do not claim migrated until verified |
| `legacy/.../crawl_page.py` | Independent read-only page crawler | Graph API or Selenium legacy crawler | Keep as isolated read-only tool until ported | `python main.py crawl-page ...` only after adapter contract exists |
| `pytest` / `.venv/bin/python -m pytest` | Unit, integration, workflow, and legacy characterization tests | 265 collected tests | Yes | Document as the single test command |

## 2. Current module inventory

| Module | Current Responsibility | Correct Layer | Problems | Action |
|---|---|---|---|---|
| `main.py` | Root delegate | Interface | Good minimal shim; old flag contract underneath | Keep |
| `app/main.py` | Parser, composition root, validation, maintenance, retry and phase controllers | Interface + bootstrap | 600 lines; concrete construction and business decisions mixed together | Split and replace as CLI implementation |
| `app/config/settings.py` | Typed environment settings and project-root paths | Config | Not always validated; overlaps browser YAML config; paths split across `data/`, root folders, and `runtime/` | Keep, centralize, extend |
| `app/config/facebook_browser.py` | Browser YAML/env settings | Config | Duplicates Settings fields and defaults | Merge into central settings |
| `app/models/workflow.py` | Workflow status, job and event records | Domain | Mixes business states with queue/lock states | Move/merge into authoritative domain model |
| `app/workflows/state_machine.py` | Transition validation | Domain rules | Queue states have no valid transitions; mixed concerns | Keep rule behavior, move and simplify |
| `app/models/results.py` | Download/AI/CDHA/Facebook/pipeline result DTOs | Application DTO/domain values | Large compatibility aggregation | Split by responsibility while retaining re-exports |
| `app/errors.py` | Typed error taxonomy | Domain/application exceptions | Better than new parallel exception packages; not used consistently | Keep as authoritative taxonomy, split only where useful |
| `app/repositories/job_repository.py` | SQLite job state, events, duplicate checks, backup | Infrastructure | SQL in legacy location; schema lacks structured retry/lease/error fields | Move/merge into one SQLite repository |
| `app/workflows/cdha_pipeline.py` | Current authoritative end-to-end state router | Application use case | Concrete adapters, Chrome, settings and review UI are directly coupled; duplicate orchestration methods; one undefined-variable branch | Replace incrementally with injected `ProcessJobUseCase` |
| `app/adapters/downloadreel_adapter.py` | Working legacy Reel wrapper and duplicate protection | Infrastructure downloader | Mutates `sys.path`; persists transitions itself; broad exception handling | Keep behavior, adapt behind port |
| `app/ai/*` | Ollama client, analyzer, parsing, capability and result models | Infrastructure analyzer + DTO | Mostly coherent; pipeline creates concrete provider internally | Keep and inject through analyzer port |
| `app/browser/cdha_client.py` | CDHA upload, polling, reconciliation, result and screenshots | Infrastructure gateway | 727 lines; repository and workflow state coupled into browser adapter | Keep behavior, extract gateway contract/state decisions |
| `app/browser/gemini_client.py` | Legacy Gemini web analysis/login | Infrastructure analyzer | Parallel to Ollama and directly persists workflow states | Retain as optional adapter, not workflow authority |
| `app/browser/facebook_client.py` | Working Facebook prepare/publish/reconcile/permalink/comment | Infrastructure publisher | 1030 lines; state persistence and business workflow mixed in browser adapter; some fixed sleeps | Keep verified behavior, split behind publisher port |
| `app/adapters/facebook_adapter.py` | Working Facebook sequence wrapper | Application-facing adapter | Persists/decides workflow transitions; likely comment URL bug | Migrate into publish use case with tests |
| `app/services/review_service.py` | Display, interactive review, edit and retry decisions | Application service + interface | Uses `input`, `print`, and subprocess directly | Split review policy from CLI prompts |
| `app/services/post_content_service.py` | Content normalization, privacy checks, screenshots and fingerprint | Application/domain service | Depends on Settings/filesystem | Separate pure content rules from artifact storage |
| `app/services/privacy_service.py` / `untrusted_content_service.py` | PII masking and untrusted input normalization | Domain/application service | Useful and tested | Keep |
| `app/services/retry_service.py` | Bounded retry policy | Application service | Not used by queue worker consistently | Keep and make authoritative |
| `app/services/frame_extraction_service.py` / `screenshot_service.py` | Media artifact generation | Infrastructure storage/media | Large and filesystem/browser aware | Keep as injected infrastructure services |
| `app/domain/*` | New Facebook job/result/types/exceptions | Domain | Depends on legacy status; duplicates authoritative types; several modules disconnected | Merge/replace, do not keep parallel models |
| `app/application/ports/*` | New Browser/Facebook/repository/queue contracts | Application ports | Missing analyzer/CDHA/artifact/full workflow contracts; queue recovery methods hidden outside protocol | Expand/replace with explicit authoritative ports |
| `app/application/use_cases/*` | New Facebook task use cases | Application | Broad catches, hard-coded paths, incomplete handler wiring | Preserve only after connection and contract repair |
| `app/infrastructure/persistence/sqlite_job_queue.py` | Durable queue and events | Infrastructure | Atomic claim but no worker/lease/heartbeat; raw state strings; separate DB/schema | Merge into authoritative persistence implementation |
| `app/infrastructure/persistence/sqlite_job_repository.py` | Bridge clean repository port to legacy repository | Infrastructure | `get_job()` always returns `None`; repository makes workflow decisions | Replace, not production-ready |
| `app/infrastructure/browser/file_browser_lock.py` | Cross-process owner-token/heartbeat/stale lock | Infrastructure browser | Strong implementation; official CLI does not use it | Keep and connect everywhere |
| `app/browser/facebook_browser_manager.py` | Central Chrome launch and Playwright CDP context | Infrastructure browser | Profile lock method unused; diagnostics HTML unsanitized | Keep, connect lock, harden diagnostics |
| `app/infrastructure/browser/cdp_connection.py` / `playwright_browser_adapter.py` | Thin browser port adapters | Infrastructure browser | Page lifecycle/name semantics incomplete; no close contract | Repair and use only if needed |
| `app/infrastructure/browser/chrome_process_manager.py` | Independent Chrome launcher | Infrastructure browser | Disconnected; duplicate; undeclared `requests` dependency | Replace/isolate |
| `app/infrastructure/facebook/*` | New Facebook port implementations | Infrastructure Facebook | Fake/unchecked success results; post adapter bypasses injection and auto-confirms publish | Replace with wrappers around verified clients |
| `app/integrations/facebook/*` | Second selector/service automation stack | Infrastructure Facebook | Click-only success, duplicate selectors, connected only to duplicate worker | Merge verified behavior or isolate |
| `workers/facebook_browser_worker.py` | Queue consumer, lock, retry classification | Interface worker | Hidden optional queue methods; queue lacks leases; no workflow heartbeat | Keep skeleton, repair contracts and persistence |
| `app/browser/facebook_browser_worker.py` + `facebook_job.py` | Second worker/store/job/status implementation | Legacy/duplicate | Parallel queue, non-atomic claim, no lease, duplicate types | Deprecate after characterization |
| `config/dependency_container.py` | New composition root for worker | Bootstrap | Constructs incomplete/fake adapters and separate databases | Replace with one bootstrap module |
| `scripts/*` | Worker/orchestrator/browser compatibility commands | Interface scripts | Several bypass official CLI or hard-code paths | Delegate/deprecate |
| `app/infrastructure/legacy/dowloadReelFB` | Historical working Reel system | Legacy infrastructure | Standalone browser/profile path remains; runtime/history mixed with code | Keep wrapped; isolate standalone execution |
| `app/infrastructure/legacy/AutoFacebook-SonMinhShare` | Historical crawler/poster | Legacy infrastructure | Crawler still Selenium fallback; compatibility producer points to incomplete duplicate stack | Keep isolated until each behavior is characterized and wrapped |

## 3. Dependency and reliability problems

### Layer boundaries

- No static import cycle was found, but dependency direction is inverted in several places.
- The new domain imports legacy `app.models.workflow.WorkflowStatus`.
- `CDHAPipeline` directly constructs download, Ollama, CDHA, Chrome, selector, Facebook, repository and review implementations.
- Browser clients and adapters make workflow-state decisions and write repositories.
- `SQLiteJobRepository` decides next workflow states from current state.
- `app.main` contains configuration checks, retry mapping and phase business rules.

### Duplicate implementations

- Two job models, two job-type enums and two active status models.
- Three job/queue persistence implementations and at least two SQLite schemas/paths.
- Two worker implementations and a hybrid script orchestrator.
- Two Facebook automation service stacks plus the verified legacy publisher client.
- Two browser configuration models and multiple browser start/stop paths.
- Ollama is the active AI path while Gemini remains a second browser-driven analysis path.

### Configuration and secrets

- Paths are usually project-root relative in Settings, but several clean modules and scripts hard-code `runtime/...`, port `9222`, or browser executable candidates.
- Official CLI does not call `Settings.validate()` consistently.
- Three cookie flows exist: legacy downloader cookie, root `Cookie.txt`, and generated `runtime/cookies.txt`; separate Gemini/CDHA injection files also exist.
- No real secret was inspected or found tracked during the filename-only audit. Runtime and `.env` remain local.
- `.gitignore` does not currently ignore all runtime lock/PID/diagnostic metadata.

### Async, timeouts and cleanup

- Blocking legacy download and HTTP calls are generally moved to threads, but synchronous polling remains in old clients.
- Several fixed sleeps remain; some are bounded polling, while others are unexplained UI settling delays.
- Browser manager cleanup disconnects Playwright but intentionally leaves shared Chrome running; scoped explicit shutdown exists.
- The robust `FileBrowserLock` is only used by worker paths, not the official direct CLI pipeline.
- Worker cleanup releases the browser lock in `finally`, but queue ownership has no lease or heartbeat.

### Persistence, retry and recovery

- Legacy job rows keep most required fields inside unstructured `data_json` rather than schema columns.
- Queue claims are atomic via `BEGIN IMMEDIATE`, but there is no `claimed_by`, `lease_expires_at`, or job heartbeat.
- Startup recovery requeues every interrupted row without checking a live worker lease.
- Queue state transitions use unchecked strings and are not governed by the domain state machine.
- The queue protocol omits retry, recovery, event and state methods used dynamically by the worker.
- Retry classification uses exception-name/message substring matching in the worker.
- External side-effect uncertainty is handled well in the verified CDHA/Facebook clients, but the new clean adapters return fake or click-only success.

### Error handling and disconnected code

- Broad exception catches remain in workflow, browser clients, use cases, diagnostics and startup paths.
- `CDHAPipeline.run_until_review()` contains an undefined `adapter` branch.
- `SQLiteJobRepository.get_job()` is a production stub returning `None`.
- Clean Facebook group/share/metadata adapters return fabricated success/results.
- `ChromeProcessManager`, several use cases, domain exception/model modules, `facebook_job_client`, and `process_file_lock` have no active internal consumer.
- `FacebookPublisherAdapter.add_permalink_comment()` resolves the persisted permalink but appears to pass the original Reel URL to the browser client.

## 4. Reconstructed current workflow

### Official direct CLI path

```text
Reel URL
→ main.py / app.main
→ DownloadReelCoordinator + wrapped legacy downloader
→ legacy JobRepository (data/jobs.sqlite3)
→ CDHAPipeline state router
→ OllamaAnalyzer (+ optional frame extraction)
→ CDHAWebClient through shared Playwright/Chrome
→ ScreenshotService
→ interactive ReviewService
→ FacebookPublisherAdapter + FacebookWebClient
→ publish reconciliation and exact permalink extraction
→ optional permalink comment
→ legacy JobRepository events/data
→ COMPLETED
```

This is the most complete and best-tested workflow. It is resumable but is concrete-coupled and bypasses the durable queue/worker for most stages.

### Hybrid orchestrator/worker path

```text
run_orchestrator.sh URL
→ run_end_to_end_worker.py
→ create/reuse legacy job
→ enqueue DOWNLOAD_REEL into runtime/queue.db
→ worker claims task and runs clean use case/adapter
→ orchestrator polls legacy repository
→ direct CDHAPipeline runs Ollama/CDHA/review
→ overridden Facebook step enqueues CREATE_POST
→ worker uses incomplete PlaywrightPostAdapter
```

This path is connected only partially. It mixes two databases and depends on an incomplete publishing adapter that bypasses the injected browser and auto-confirms.

| Workflow Step | Current Status | Evidence |
|---|---|---|
| Input | Connected and working | Root CLI accepts Reel URL and resume/retry flags |
| Job creation | Connected and working in direct path; duplicated in hybrid path | Legacy repository creates job; scripts also create queue job models |
| Queue | Connected but incomplete | Atomic claim/events/retry exist; lease/heartbeat/worker identity do not |
| Worker | Connected but incomplete | Processes three Facebook job types; hidden protocol methods and incomplete adapters |
| Reel download | Connected and working in direct path; duplicated | Legacy wrapper is validated and tested; clean worker adapter uses separate cookies/paths |
| Content collection | Legacy only / disconnected from main clinical workflow | Page crawler and old interaction/crawl features are isolated under legacy |
| AI analysis | Connected and working in direct path | Ollama path is tested; not scheduled as a worker unit |
| CDHA processing | Connected and working in direct path | Upload/result/reconciliation logic exists; not queued |
| Human review | Connected and working | Hard gate persists WAITING_FOR_REVIEW; UI is embedded in service/workflow |
| Facebook publishing | Connected and working in direct path; unsafe/incomplete in worker path | Verified client exists; clean queue adapter auto-confirms and lacks reliable verification |
| Result persistence | Connected but split | Legacy job/event DB plus separate queue DB/table(s) can diverge |
| Cleanup | Connected but incomplete | Browser/lock cleanup exists in some paths; legacy cleanup is separate and runtime roots differ |

## Baseline conclusion

The repository already has most verified feature implementations, but it does not yet have one architecture. The safest migration direction is to keep the verified direct workflow behavior, introduce authoritative domain/contracts and one persistence model around it, connect every stage through injected ports, then move scheduling and execution to the durable worker without substituting incomplete adapters. Duplicate stacks must be deprecated only after tests prove the authoritative replacements are connected.
