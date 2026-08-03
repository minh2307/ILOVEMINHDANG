# Progress Log — Unified Browser/Profile/Cookie Configuration

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
