# Progress Log — Prompt 5 Facebook Publication Verification

## Session: 2026-08-03

- **Pha 1:** `in_progress`
- Đọc đầy đủ `planning-with-files` và `systematic-debugging`.
- Xác nhận prompt đã chuyển từ Prompt 4 sang Prompt 5 (1.258 dòng).
- Bảo toàn toàn bộ worktree Prompt 1–4; chưa sửa production.
- Đọc phần đầu Prompt 5, ghi nhận yêu cầu result/state/validation/fingerprint/
  durable attempt và nguyên tắc không xem click/navigation/dialog đóng là success.
- Đọc trọn Prompt 5, bao gồm 19 pha kỹ thuật, test matrix, safe/live boundary và
  cấu trúc báo cáo cuối 15 phần.
- Bắt đầu persisted-evidence inventory và official `confirm-publish` call graph.
- Canonical DB là `data/jobs.sqlite3` (320 KiB); các DB legacy phần lớn 0 byte.
- Query read-only đầu tiên không chạy vì host không có alias `python`; chuyển
  sang `python3`, không thay đổi DB/environment.
- Baseline sandbox dừng ở khoảng 27% không có failure/summary sau stream-fd
  errors; đã dừng sạch và chuyển sang cùng suite ngoài sandbox để phân biệt lỗi
  môi trường với regression.
- Baseline ngoài sandbox hoàn tất **388 passed in 5.28s**.

---

# Progress Log — Prompt 4 CDHA/Browser Reliability

## Session: 2026-08-03

- **Pha 1:** `in_progress`
- Đọc đầy đủ skill `planning-with-files`.
- Xác nhận prompt hiện tại là Prompt 4 (945 dòng), khác với chat tham chiếu.
- Ghi nhận và bảo toàn 15 modified/untracked files của Prompt 3.
- Khởi tạo kế hoạch Prompt 4 và bắt đầu failure-evidence inventory.
- Đọc trọn 945 dòng prompt và thực hiện source/runtime inventory ban đầu.
- Xác nhận repo đã có queue lease/heartbeat và browser-lock heartbeat, nhưng vẫn
  cần audit call graph, CDHA selector/state logic, persisted failures và timeout
  ownership trước khi thiết kế test đỏ.
- Định vị các SQLite/runtime artifacts và đọc các đường active đầu tiên:
  `CDHAWebClient`, browser manager và durable queue. Chưa sửa production.
- Xác nhận composition root dùng `data/jobs.sqlite3`, settings/timeouts và các
  helper upload/analysis active. Lần query DB bằng CLI thất bại vì host không có
  `sqlite3`; đã ghi lỗi và chuyển sang Python stdlib read-only.
- Query canonical DB read-only bằng Python stdlib và lập failure inventory từ
  11 jobs/529 events/3 queue rows, logs và JSON diagnostics.
- Xác nhận persisted evidence cho browser/page closed, `#btnComplete`
  missing/hidden/disabled, upload uncertain, auth required, unknown states và
  queue timeout 180 giây. Chưa thay đổi production.
- Hoàn tất audit đầu tiên cho selector/error/port/worker và test coverage hiện
  hữu; xác định các gap cụ thể nhưng chưa đề xuất/sửa trước baseline.
- Baseline attempt 1 không bắt đầu vì temporary venv từ phiên trước đã bị dọn.
  Đây là lỗi môi trường trước collection; không có test fail và không sửa source.
- `.venv` dự án được xác nhận ABI/environment-stale (Python 3.14, thiếu pytest);
  không sửa nó. Đã tạo `/tmp/minhdang-prompt4-venv` bằng Python 3.13.5.
- Dependency install lần đầu bị sandbox DNS, lần retry được phê duyệt thành công;
  18 packages cài dưới `/tmp`, workspace không bị thay đổi.
- Restricted-sandbox baseline dừng khoảng 30% không có exit code; cùng suite
  ngoài sandbox hoàn tất **357 passed in 5.51s**.
- **Pha 1 complete; Pha 2 in_progress.**
- Pha 2 audit xác nhận browser manager active, temporary-page gap, existing
  official recovery commands và khoảng trống adapter-level CDHA idempotency.
- Hoàn tất pattern/root-cause comparison; bắt đầu viết regression tests đỏ cho
  lifecycle, selector/state, timeout/idempotency và queue lease.
- Regression file mới fail collection như mong đợi vì contract chưa tồn tại.
- Implemented browser health/page ownership, selector observations, CDHA state
  wait, typed timeout settings, queue stage/lease-loss/max-attempt recovery,
  CDHA fingerprint/reconciliation guard và closed-page-safe diagnostics.
- Verification hiện tại: **13 Prompt 4 tests passed**, **86 focused tests
  passed**.
- **Pha 2 complete; Pha 3 in_progress.**
- Expanded Prompt 4 suite lên 27 tests cho hidden/disabled/missing/fallback,
  auth taxonomy, cancellation, external-ID reuse, safe inspect và cleanup.
- Full suite attempt: **383 passed, 1 failed** do diagnostics compatibility;
  root cause được sửa tối thiểu, focused rerun **28 passed**.
- **Pha 3 complete; Pha 4 in_progress.**
- Hoàn tất official `inspect-browser`/`inspect-queue`, typed authentication,
  stage-aware heartbeat và cập nhật env/runbook.
- Focused verification tiếp theo chưa chạy vì `/tmp` venv bị dọn giữa các lượt;
  đã ghi nhận là lỗi môi trường trước collection.
- Tái tạo venv tạm và chạy focused suite: **105 passed in 1.06s**.
- **Pha 4 complete; Pha 5 in_progress.**
- Removed active `FacebookJobClient` hard-coded 180-second wait; it now uses the
  typed worker-stage timeout.
- Final full suite: **388 passed in 4.90s**.
- Compile, shell syntax, static lifecycle/selector audits and diff check pass.
- Real Quick: **FAIL**, exit 1, missing `ffmpeg`; report mode 0600 and secret
  scan clean.
- `config` and `inspect-queue` succeeded. `inspect-browser` sandbox run produced
  no exit/output, so the read-only approved rerun was used and reported
  DISCONNECTED/CDP false/no PID/no lock without starting Chrome.
- Full preflight not executed because authenticated external access was not
  newly authorized.
- Added `docs/cdha-browser-reliability-report.md`.
- **Pha 5–6 complete.**

---

# Progress Log — Unified Browser/Profile/Cookie Configuration

## Session: 2026-08-03 — Referenced-chat continuation

- **Status:** complete
- Re-entered the referenced-chat task from the actual dirty worktree and read
  `promt.md`, the planning files, and both applicable skills completely.
- Confirmed the Prompt 3 implementation is present as 14 modified files plus
  two new files; `git diff --check` is clean and the structured preflight
  contracts/official runner are present.
- Verification attempt through the temporary Python environment emitted
  `Failed to create stream fd: Operation not permitted` before Python could
  report its version. This matches the previously documented sandbox
  infrastructure failure; no code or environment change was made. Following
  `systematic-debugging`, the next step is a minimal environment-boundary probe,
  not a source fix.
- Recreated `/tmp/minhdang-preflight-venv` with Python 3.14. Dependency install
  first failed on sandbox DNS, then succeeded through the approved network path;
  the repository `.venv` remains untouched.
- Focused Prompt 3 verification passed **43 tests**. The restricted-sandbox full
  suite hung reproducibly around 20% at a downloader adapter after stream
  creation failures; the identical approved out-of-sandbox suite passed
  **356/356 in 4.57s**.
- Source compile, tracked shell syntax, static preflight ownership and
  `git diff --check` passed.
- Real Quick returned **FAIL** and wrote
  `runtime/diagnostics/preflight/preflight_quick_20260803T081304.628572Z.json`;
  the required blocker is missing `ffmpeg`.
- Real Full was authorized and executed read-only. It returned **FAIL** and
  wrote
  `runtime/diagnostics/preflight/preflight_full_20260803T081355.550403Z.json`.
  Browser startup passed; blockers were missing `ffmpeg`, Ollama unavailable,
  Facebook `LOGIN_REQUIRED`, and CDHA authentication not ready.
- Full diagnostics showed a localized CDHA login title classified as `UNKNOWN`.
  Added a regression test first (expected import failure), then introduced one
  shared Full classifier that checks URL, localized title, canonical login
  selectors, security markers and the official auth fallback. Focused suite is
  now **44 passed**.
- Error log: initial source search included nonexistent `app/cdha`; active code
  was found at `app/browser/cdha_client.py`, and the bad path was not retried.
- Final post-fix verification: **44 focused passed**, **357 full-suite passed
  in 4.36s**, compile, tracked shell syntax, single-ownership static audit and
  `git diff --check` passed.
- Repeated live Full after the detector fix: exit code **1**, overall **FAIL**,
  browser start passed, Facebook and CDHA both explicitly
  `LOGIN_REQUIRED`. Latest report:
  `runtime/diagnostics/preflight/preflight_full_20260803T081651.486122Z.json`.
- Latest Full report and both browser metadata artifacts are mode `0600`;
  secret/HTML/patient-data pattern scans returned no matches.
- Repeated referenced-chat request audit: `promt.md` SHA-256 is
  `7520e19808ae97e20b4630029532a5b498e6450830d846e352601a6a30589b74`;
  the active six-phase plan remains complete, the latest Full report exists,
  and `git diff --check` remains clean. No duplicate implementation changes
  were made.
- Re-read the complete `planning-with-files` skill and the existing planning
  files before taking implementation action.
- The workspace is currently clean according to `git status --short`.
- Existing records say the latest `promt.md` implementation reached 345 passing
  tests, but this session will verify the current prompt/source/tests directly
  rather than relying on the referenced-chat summary.
- Read the current 1,072-line `promt.md`; it is Prompt 3 for an authoritative
  Quick/Full readiness preflight and supersedes the older referenced prompt.
- Added a new six-phase plan and initial prompt findings without overwriting
  prior session history.
- Read the complete `systematic-debugging` skill because Prompt 3 requires a
  misleading-PASS reproduction before any fix.
- Located the current flat preflight implementation, official CLI hook and
  existing tests; no production implementation changes have been made yet.
- Confirmed the root cause at the official CLI boundary: `worker
  --preflight-only` returns 0 after a non-exception report whose
  `ollama_checked` field is false.
- Error log: attempted to inspect `app/browser/file_browser_lock.py`, which does
  not exist; the active implementation is
  `app/infrastructure/browser/file_browser_lock.py`. The incorrect path will not
  be retried.
- Baseline attempt 1 failed before collection because `.venv/bin/pytest` has a
  stale absolute shebang pointing at `/media/.../.venv/bin/python3`, while the
  workspace is mounted at `/run/media/...`.
- Inspection confirmed `.venv/bin/python -> python3 ->
  /usr/bin/python3` remains valid. The next attempt will invoke the same
  environment as `.venv/bin/python -m pytest` rather than modify the user's
  virtualenv.
- Baseline attempt 2 also failed before collection: the venv was created for
  Python 3.12, but `/usr/bin/python3` now resolves to Python 3.14.4, so the
  interpreter does not load `.venv/lib/python3.12/site-packages`.
- The installed test/dependency packages are still present under the Python
  3.12 site-packages directory. No symlink or venv file has been modified.
- `/usr/bin/python3.12` is no longer installed; only Python 3.14 is available.
- `uv` is available. The verification environment will therefore be recreated
  under `/tmp` from `requirements.txt`, preserving the user's broken-but-
  untouched `.venv`.
- Temporary venv attempt 1 failed because uv's default cache under the read-only
  home directory could not be created; retry used `/tmp/minhdang-uv-cache`.
- Dependency installation attempt 1 then failed on sandbox DNS access to PyPI.
  The approved network retry succeeded and installed all requirements into
  `/tmp/minhdang-preflight-venv`; repository files and `.venv` were unchanged.
- Baseline attempts inside the sandbox were terminated around 20% by repeated
  `Failed to create stream fd` infrastructure errors; the JUnit artifact was
  never written, so no result was inferred from those runs.
- Approved out-of-sandbox baseline completed successfully: **345 passed in
  4.69s** using `/tmp/minhdang-preflight-venv/bin/python -m pytest -q`.
- Added the first verdict/completeness regression tests before implementation.
  Expected red result: collection fails because `CheckStatus` and the structured
  report contract do not yet exist in `app.preflight`.
- Implemented the structured result/verdict contract and adapted the legacy
  runner return value. Focused verification: **6 passed** across new verdict
  tests and the existing preflight mutation guard.
- Added preflight-specific typed timeouts/report path, official Ollama adapter
  readiness hooks, the Quick local matrix, Full read-only Ollama/browser/auth
  probes, lock inspection, sanitized diagnostics/reporting and human formatter.
- First post-runner verification command referenced two nonexistent test files
  (`tests/test_settings.py`, `tests/test_ollama_client.py`), so pytest correctly
  stopped before collection. The command will not be repeated; actual related
  tests were located by filename.
- `compileall app` succeeded for production source but also traversed legacy
  embedded virtualenvs and emitted unrelated Python 3.14 warnings. Final compile
  will target tracked/source directories without embedded environments.
- Updated the legacy missing-Playwright test to the new structured contract and
  gated composition-root construction behind local prerequisites, preserving
  the no-database/no-lock mutation invariant. Focused suite: **37 passed**.
- Registered the single official `preflight --mode quick|full [--verbose]`
  command before runtime-directory creation, mapped report verdicts to CLI exit
  codes, and made Worker startup require a non-failing Full report.
- CLI and related focused verification now passes: **38 passed**.
- Added Quick side-effect boundary, official Ollama success/timeout, live-lock
  preservation and report-redaction regression tests. Focused suite: **43
  passed**.
- Full repository suite after implementation: **356 passed in 4.39s**.
- Real Quick preflight executed through `python -m app.main`: exit code **1**,
  overall **FAIL** because `ffmpeg` is absent. Canonical cookie absence and four
  inactive legacy paths are WARN; external checks are explicitly optional
  SKIPPED in Quick mode.
- Quick report was written under `runtime/diagnostics/preflight/`; manual and
  pattern inspection found no cookie values, session IDs, passwords,
  authorization headers or access tokens.
- Real Full preflight escalation was rejected because it would use the
  authenticated Chrome profile and contact Ollama, Facebook and CDHA. No
  workaround or indirect live probe was attempted. Final evidence must mark
  external readiness as not executed/blocked, never PASS.
- Final verification attempt 1 stopped before collection because the execution
  environment had cleaned `/tmp/minhdang-preflight-venv` and its uv cache.
  This is an ephemeral-environment error, not a test failure; the repository
  `.venv` remains untouched.
- Recreated the temporary environment and completed final verification:
  **43 focused passed**, **356 full-suite passed in 4.77s**, tracked/source
  compile passed, all tracked shell scripts passed `bash -n`, static ownership
  audit found one `PreflightReport` and one `run_preflight`, and
  `git diff --check` passed.
- Final real Quick command returned exit code 1 and wrote
  `runtime/diagnostics/preflight/preflight_quick_20260803T065024.541901Z.json`;
  the only required failure is missing host `ffmpeg`.
- Added the required 12-section evidence report at
  `docs/preflight-readiness-report.md` and updated README, operations and the
  Vietnamese run guide.

## Session: 2026-07-30

### Phases 1–5: Inventory, Integration, Migration, Verification

- **Status:** complete
- Read all 628 lines of `promt.md` and the full `planning-with-files` skill.
- Preserved the large uncommitted worktree and all runtime/profile/cookie data.
- Confirmed root cause: Worker used Settings profile `cdha_automation`, while browser CLI used YAML profile `facebook`; official downloader also reached a legacy implicit cookie fallback.
- Implemented one typed Settings source, derived browser config, one injected manager/lock, explicit Reel cookie dependency, sanitized config/fingerprint, preflight, migration warnings, tests, and documentation.
- Main CLI, browser CLI, and preflight resolved the same profile, lock, cookie status, and fingerprint. No browser was opened.
- Final verification: **345 passed, 0 failed, 0 skipped** in 7.38s; compile, shell syntax, preflight, config parity, secret filename scan, and `git diff --check` passed.

### Error Log

| Error | Attempt | Resolution |
|---|---:|---|
| `bwrap: loopback: Failed RTM_NEWADDR` in patch/read helpers | Multiple | Used approved scoped execution and mechanical fallback only for known files |
| First full suite after integration: 95 failures from `FB_POSTER_PROFILE` conflict | 1 | Added dotenv cleanup; focused sequence passed but full suite proved the hypothesis incomplete |
| Later full suite: 72 failures from the same conflict | 2 | Found legacy import-time `load_dotenv()` mutation; replaced it with local `dotenv_values()` lookup |
| Reproduction sequence after root fix | 3 | 161 passed |
| Final full suite | 4 | 345 passed |

---


- **Phase 1–5 status:** complete.
- Đọc skill `planning-with-files` đầy đủ và thêm kế hoạch mới.
- Xác nhận `promt.md` hiện là yêu cầu hợp nhất CLI/state machine, khác với prompt
  browser manager trong chat tham chiếu.
- Ghi nhận worktree có refactor lớn chưa commit; không reset/xóa thay đổi.
- Bắt đầu đối chiếu prompt với CLI, workflow và test hiện tại.
- Baseline hiện tại: **295 passed in 7.47s**.
- `python main.py --help` xác nhận official subcommands và toàn bộ legacy flags
  đang đồng thời xuất hiện ở giao diện cấp cao nhất.
- Regression đỏ ban đầu: lifecycle test không collect vì bốn official use case
  chưa tồn tại; repository tests tái hiện transition message thiếu job/reason và
  output/artifact JSON bị đảo.
- Đã thêm Resume/Review/ConfirmPublish/GetJobStatus use cases; chuẩn hóa retry
  metadata, max-attempt, duplicate request và queue key theo attempt.
- Focused verification: lifecycle **8 passed**, repository **9 passed**, compile
  `app` pass.
- Official CLI/legacy delegation regression suite: **14 passed** sau khi sửa
  dispatch order và missing confirm return.
- Full suite sau lifecycle + CLI convergence: **318 passed in 6.76s**; compile
  `app workers config scripts` pass; shell syntax pass.
- Legacy stage-only flags giờ fail-safe; bốn compatibility flags chính delegate
  qua official parser/use case và test chứng minh không gọi legacy pipeline.
- Bổ sung worker-boundary retry: definitive pre-click Facebook failure đi qua
  `RETRY_PENDING`; post-click failure thành `FACEBOOK_PUBLISH_UNCERTAIN` và không
  tự retry.
- Static policy xác nhận official graph không import `CDHAPipeline`, active source
  có đúng một `JobStatus` enum và một transition map.
- Cập nhật README, hướng dẫn tiếng Việt, pending-action strings và báo cáo đầy đủ
  tại `docs/official-cli-state-machine-report.md`.
- Final verification: **332 passed in 7.25s**, 0 failed, 0 skipped; compile,
  shell syntax, `git diff --check`, static command audit đều pass.
- Lỗi: `apply_patch` trong sandbox gặp `bwrap: loopback: Failed RTM_NEWADDR`;
  chuyển sang `git apply` sau hai lần thất bại của helper và một patch context lỗi.
- Lỗi thao tác: một bản `git apply` chứa marker không thuộc unified diff và một
  heredoc nhiều diff đóng sớm; phần findings đã được áp dụng trước khi shell lỗi.
  Chuyển sang mỗi lệnh một unified diff, kiểm tra context hiện tại trước khi thử lại.
- Lỗi implementation: patch transition validator cắt thiếu phần đóng exception,
  compile báo `SyntaxError` tại dòng 77; đọc đúng dòng, hoàn tất biểu thức, rồi
  cả compile và focused tests pass. Hai patch repository context lệch tạo `.rej`;
  file reject do phiên này tạo đã được chuyển sang `/tmp`.
- CLI regression đầu tiên có 6 fail: confirm branch thiếu `return`, còn legacy
  resolver chạy sau khi tạo maintenance repository. Chuyển resolver lên trước
  repository và thêm return; rerun 14/14 pass.
- Full suite sau đổi pending-action có 1 fail vì characterization test còn đòi
  `--review-job`; cập nhật expected sang `review --job-id`, rerun 332/332 pass.
- Final status phát hiện 5 file `.orig` do `patch` tạo trong phiên; chuyển toàn bộ
  sang `/tmp`, không xóa và xác nhận workspace không còn `.orig`/`.rej` mới.

---

# Progress Log — Unified Automation Refactor

## Session: 2026-07-29

### Phase 1: Inventory and Safety Baseline

- **Status:** in_progress
- Actions taken:
  - Đọc đầy đủ yêu cầu trong attachment.
  - Đọc skill `planning-with-files` và khởi tạo bộ nhớ làm việc bền vững.
  - Giữ nguyên kế hoạch cũ, thêm kế hoạch refactor 10 pha.
  - Ghi nhận Git worktree đang có một lượng lớn thay đổi chưa commit từ trước.
  - Lập danh sách toàn bộ file hiện tại ngoài runtime và đọc các Markdown hiện có.
  - Đọc requirements, `.env.example`, pytest config, Python/shell entrypoint signals.
  - Chạy toàn bộ baseline tests: 260 pass, 5 fail.
  - Điều tra root cause theo systematic-debugging và sửa tối thiểu hai contract regression có sẵn.
  - Lập inventory line-count, definitions, internal import edges, status representations và SQL ownership.
  - Đọc đầy đủ active entrypoint/worker/orchestrator/container và `CDHAPipeline` để dựng call chain thực tế.
  - Đọc state enum/machine, legacy repository, queue, repository adapter, domain models, ports, use cases và worker recovery.
  - Đọc typed settings, browser config/manager/lock/CDP adapters và quét launch/sleep/broad-exception/hard-coded-path patterns.
  - Phân loại clean Facebook adapters/integrations, duplicate browser-worker stack và working legacy downloader/Facebook adapter/client paths.
  - Đọc toàn bộ imported docs từ HEAD, diff/move summary, `.gitignore`, runtime metadata và phần còn lại của run guide.
  - Chạy AST dependency analysis, filename-only secret scan, ignore checks, compileall và pytest collection (265 tests).
  - Xác nhận full clean baseline: 265 passed in 6.36s.
  - Viết mandatory pre-refactoring report tại `docs/pre-refactoring-report.md`.
- Files created/modified:
  - `task_plan.md` (appended)
  - `findings.md` (created)
  - `progress.md` (created)

## Test Results

| Test | Command | Expected | Actual | Status |
|---|---|---|---|---|
| Baseline | `.venv/bin/pytest -q` | Ghi nhận toàn bộ suite hiện tại | 260 passed, 5 failed (6.48s) | fail (pre-existing worktree) |
| Facebook page state | `.venv/bin/pytest -q tests/test_facebook_page_state.py` | Detector nhận navigation shell là logged in | 13 passed | pass |
| Ollama parser contracts | `.venv/bin/pytest -q tests/test_ollama_output_parser.py tests/test_ai_security_privacy.py tests/test_clinical_factors.py tests/test_phase3_contract.py tests/test_phase3_browser.py` | Giữ format CDHA và downstream behavior | 53 passed | pass |
| Compile baseline | `.venv/bin/python -m compileall -q app workers config scripts` | Không syntax/import compilation error | Pass | pass |
| Test collection | `.venv/bin/python -m pytest --collect-only -q` | Collect toàn bộ suite | 265 collected | pass |
| Clean behavioral baseline | `.venv/bin/pytest -q` | Không failure trước structural refactor | 265 passed in 6.36s | pass |
| Final verification | `.venv/bin/python -m compileall -q app workers config scripts && .venv/bin/pytest -q && git diff --check` | Kiến trúc tích hợp không regression | 280 passed in 6.48s; compile/diff pass | pass |

### Phases 2–10: Integration and verification

- **Status:** complete
- Hợp nhất domain types, trạng thái, transition rules và error taxonomy; legacy names chỉ re-export.
- Chuyển SQLite repository thật vào infrastructure; bổ sung additive schema metadata và artifact paths.
- Bổ sung queue atomic claim, worker ownership, lease, heartbeat, expired-only recovery và bounded retry.
- Tạo `ProcessJobUseCase`, create/retry/scheduler use cases và verified one-stage adapter.
- Production container chỉ dispatch `PROCESS_WORKFLOW`, dùng chung `DATABASE_PATH` và một browser manager/profile.
- Kết nối browser manager cleanup vào worker lifecycle; bật profile lock và redaction HTML diagnostics.
- Thêm subcommand CLI chính thức, giữ flags/scripts cũ dưới dạng compatibility/deprecation delegates.
- Loại bỏ fake-success và implicit auto-publish khỏi adapter không được xác minh; cô lập browser job stack/integrations cũ dưới legacy.
- Thêm dry-run-first migration tool cho `runtime/queue.db`; source được mở read-only và không bị xóa/sửa.
- Viết README, architecture, folder structure, workflow/state machine, migration và operations/manual checklist.
- Thêm test workflow fake full path, manual gates, stage failure, scheduler idempotency, atomic claim, fresh/expired lease, resource cleanup, artifact persistence, typed settings, exact permalink comment và legacy migration.

## Error Log

| Timestamp | Error | Attempt | Resolution |
|---|---|---:|---|
| 2026-07-29 | `bwrap: loopback: Failed RTM_NEWADDR` khi đọc file trong sandbox | 1–2 | Chuyển sang read-only execution được phê duyệt |
| 2026-07-29 | 3 Facebook state tests và 2 Ollama formatting tests fail | 1 | Bắt đầu root-cause investigation theo systematic-debugging; chưa sửa |
| 2026-07-29 | Facebook detector thiếu generic navigation indicator | 1 | Thêm indicator, 13 focused tests pass |
| 2026-07-29 | Ollama formatter lệch contract newline | 1 | Khôi phục format verified từ HEAD, 53 focused tests pass |

## 5-Question Reboot Check

| Question | Answer |
|---|---|
| Where am I? | Pha 1 — Inventory và baseline |
| Where am I going? | Pha 2–10 theo `task_plan.md` |
| What's the goal? | Hợp nhất repo thành một ứng dụng tự động hóa coherent, testable, resumable |
| What have I learned? | Xem `findings.md` |
| What have I done? | Đọc yêu cầu và khởi tạo audit trail |

### Session: CDHA clinical-summary bug and real E2E

- **Phase 1 status:** in progress.
- Đọc toàn bộ attachment khả dụng; ghi nhận file bị cắt giữa model definition.
- Đọc systematic-debugging và planning-with-files skills.
- Truy vết sơ bộ các symbol/selectors từ CDHA extraction đến Facebook publish.
- Chưa sửa production code trước khi xác nhận root cause.
- Root cause confirmed across selector → extractor → DTO/persistence → formatter → review → final click boundary.
- Added failing regression tests for structured summary normalization/rejection, nested DOM extraction, exact result URL, measurement display, and final publish guard.
- Expected red run: collection fails with `ModuleNotFoundError` for the not-yet-implemented `CDHAClinicalSummary` model.
- Implemented the structured domain summary contract; its 7 focused normalization/validation tests pass.
- Implemented contextual nested-node extraction, label normalization, exact analysis URL/raw-field DTO serialization, and stronger selectors; 25 CDHA/domain focused tests pass without changing source measurements.
- Added strict formatter/summary validation, exact source URLs, Vietnamese/PII/absolute-claim checks, configured hashtags, test-target routing, and test-mode comment suppression.
- Error: first final-publish guard patch missed the exact `_source_url` body context; inspected current source and will retry with the precise context rather than repeating the failed patch.
- Final-publish guard applied successfully on the second, context-correct patch.
- First integrated focused run: 4 legacy browser tests failed because they intentionally published arbitrary captions; upgraded those fixtures to valid generated clinical posts. Rerun: **74 passed**.
- Expanded focused suite including test-target settings: **83 passed**.
- Complete repository suite after implementation: **294 passed in 6.55s**.
- Real E2E preflight passed core local checks, but loaded configuration is production mode (`TEST_MODE=false`) with no separate test target; publish will remain blocked for safety.
- Created/queued real job `1aab9d248a1b46338d592754d53011d8`; download succeeded, then official job failed on an inactive legacy metadata/comments child queue timeout after 180 seconds.
- Added a failing regression reproducing the inactive child-queue dependency (`unexpected keyword` before implementation), then added official caption-only mode. Full downloader reliability suite: **18 passed**.
- Retried real job: download/sidecar/frame extraction/Ollama/CDHA/extraction/screenshots all completed; persisted state reached `WAITING_FOR_REVIEW` with exact real values and URL.
- Process-local validated auto-review succeeded and queued `APPROVED` work. External Facebook execution is paused because no separate E2E target is configured; production `/me` is not being treated as a test target.
- Offline render of the persisted real result passed final publish validation and produced the exact required Vietnamese structure with no leaked English labels.
- Final verification initially saw one host-dependent CDP unit failure because the live E2E browser occupied port 9222; isolated the test's owner probe without weakening production. Focused test passed and final full suite is **295 passed in 6.86s**.
- Wrote `docs/cdha-clinical-summary-e2e-report.md` with root causes, actual output, workflow evidence, blocker and safe continuation.
- Final queue verification: original `CREATED` work item is `FAILED` with preserved timeout evidence, retry work item is `COMPLETED`, and `APPROVED` work item remains unclaimed in `CREATED` state.
- Read-only diagnostic errors: system `sqlite3` CLI was unavailable; first built-in query used the wrong legacy table name. Inspected `SQLiteJobQueue` schema and successfully queried authoritative table `queue` instead.
