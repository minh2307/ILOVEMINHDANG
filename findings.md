## Session 2026-07-30 — Unified Browser/Profile/Cookie Configuration

- `promt.md` đã thay đổi sau phiên CLI/state-machine; prompt mới có 628 dòng và
  yêu cầu hợp nhất Chrome profile/session/cookie/configuration.
- Worktree vẫn là refactor lớn chưa commit; không reset, không xóa profile,
  cookie, database, download, screenshot, log hay session file.
- Baseline gần nhất trước prompt mới là **332 passed**, compile/shell/diff pass;
  cần chạy lại sau thay đổi và không dựa vào báo cáo cũ để tuyên bố hoàn tất.
- Prompt bắt buộc bắt đầu từ inventory mọi profile/cookie path và mọi lời gọi
  `FacebookBrowserConfig.load()` / `from_settings()`, rồi truy official
  composition root để phân biệt active với legacy/test/dead code.
- Một typed settings source phải cấp cùng cấu hình tuyệt đối cho official CLI,
  Worker, browser CLI/start-check-stop scripts, Reel downloader, Facebook
  publisher, CDHA, preflight, tests và docs.
- Cookie dùng cho Reel download và persistent Chrome session dùng cho browser
  publishing/CDHA là hai cơ chế xác thực khác nhau; không được suy luận cookie
  file tự động đăng nhập browser nếu code không import cookie vào context.
- Config inspection/startup diagnostics chỉ được in path/trạng thái sanitized,
  không được đọc/in secret; fingerprint phải chỉ hash cấu hình sanitized.
- Profile/cookie legacy chỉ được cảnh báo và hướng dẫn migration; không được tự
  copy/move/merge/delete hay silently fallback theo file tồn tại.
- Root cause đã xác nhận từ `.env` và call graph: official Worker qua
  `Settings.from_env()` resolve `runtime/chrome_profiles/cdha_automation`, còn
  browser CLI qua YAML/`FacebookBrowserConfig.load()` resolve
  `runtime/chrome_profiles/facebook`.
- Chọn canonical profile `runtime/chrome_profiles/cdha_automation` vì đây là
  profile của official integrated Worker; profile `facebook` là legacy cần cảnh
  báo migration, không được tự di chuyển/hợp nhất.
- Baseline trước thay đổi prompt mới: **332 passed in 7.48s**.
- Official Reel dependency flow là `DependencyContainer -> VerifiedWorkflowStages
  -> DownloadReelAdapter -> legacy fb_downloader -> download_manager/yt-dlp`.
  `PlaywrightReelAdapter` không được import trong official flow.
- Canonical cookie sẽ là `runtime/auth/facebook_cookies.txt` (Netscape), truyền
  qua typed settings vào đúng downloader; browser publisher và CDHA dùng profile
  persistent, không dùng cookie file này.
- `git ls-files` xác nhận không có `Cookie.txt`, canonical cookie,
  `runtime/cookies.txt`, legacy downloader cookie hay storage-state nào đang
  được Git theo dõi.
- `FileBrowserLock` đã có atomic metadata, PID/create-time/cmdline identity,
  heartbeat và stale recovery; cần đổi `browser_profile` metadata từ nhãn
  `facebook` sang absolute canonical path và dùng chính lớp này trong browser CLI.


- `promt.md` hiện mô tả một yêu cầu mới: `app/main.py` phải là composition root
  chính thức theo subcommand, còn legacy chỉ delegate/deprecate.
- Worktree đang có refactor lớn chưa commit từ các phiên trước; mọi file
  modified/deleted/untracked hiện hữu được xem là baseline cần bảo toàn.
- Các planning files hiện chứa nhiều phiên trước; kế hoạch mới được thêm vào
  thay vì ghi đè lịch sử.
- Báo cáo cũ tuyên bố 280/295 tests pass, nhưng yêu cầu hiện tại cần được xác minh
  lại từ source và test hiện thời, không dựa trên tóm tắt chat.
- Definition of Done yêu cầu các lệnh create/worker/review/confirm-publish/retry/
  resume/status đều đi qua official application use cases; legacy flags chỉ là
  wrapper cảnh báo deprecation.
- Transition tối thiểu phải cho phép `FACEBOOK_PUBLISH_FAILED -> RETRY_PENDING ->
  QUEUED` và `PUBLISHED -> COMPLETED`, đồng thời từ chối mọi đường failed/review/
  publishing đi thẳng tới `COMPLETED`.
- Retry phải có metadata/attempt/idempotency và không tự đăng lại khi kết quả
  Facebook chưa chắc chắn; resume phải dựa trên state/artifact đã persist.
- Quy trình debug bắt buộc: tái hiện ổn định, truy call chain và root cause, tạo
  regression test đỏ, sau đó mới sửa tối thiểu.
- `app/main.py` hiện ghép cả legacy flags và official subcommands trong cùng parser.
  Legacy `--reel-url/--resume-job/--continue-approved-job` vẫn trực tiếp dựng
  `ChromeManager` + `CDHAPipeline`, nên vẫn là orchestration path độc lập.
- Official `review` gọi trực tiếp `ReviewService`; `resume` chỉ gọi scheduler;
  `confirm-publish` tự xử lý gate rồi gọi scheduler. Chưa có Review/Resume/
  ConfirmPublish/Status application use cases tương ứng.
- `RetryJobUseCase` chỉ ghi `retry_step` và `triggered_by`; chưa tăng/enforce
  attempt, chưa lưu previous failure/reason/requested/next retry/max attempts,
  và duplicate request chỉ dựa vào queue scheduler.
- `ProcessJobUseCase` đã là state-aware orchestrator cho official worker và chỉ
  hoàn tất từ `COMMENT_ADDED`; nó chuyển in-flight state qua `RETRY_PENDING` để
  recovery. Tuy nhiên official adapter vẫn bọc `CDHAPipeline` cho side effects.
- `JobStatus` là enum domain duy nhất và `WorkflowStatus` chỉ là alias. Legacy
  browser queue vẫn có enum riêng nhưng nằm trong `infrastructure/legacy`.
- Transition map đã chặn `FACEBOOK_PUBLISH_FAILED -> COMPLETED`, nhưng error hiện
  chỉ nêu old/new state; chưa có job ID và reason như contract mới.
- `JobRepository.transition()` dùng một câu `UPDATE` atomic sau validation, nên
  invalid transition hiện không persist partial status/event.
- Phát hiện lỗi persistence ảnh hưởng resume: thứ tự values cho
  `output_payload_json` và `artifact_paths_json` đang đảo nhau (`artifacts` được
  ghi vào output, `payload` được ghi vào artifact paths). Object trả ngay che lấp
  lỗi; chỉ lần đọc lại từ SQLite mới lộ contract sai.
- `attempt_count` chỉ dùng `MAX(current, attempt)`; Retry use case không truyền
  attempt mới nên retry request hiện không tăng attempt lần nào.

---
- Final implementation đã giải quyết các gap trên: retry tăng đúng một lần, giới
  hạn max attempts, persist metadata và dùng queue key theo attempt.
- Official CLI commands đều gọi container-owned use cases; legacy workflow flags
  được resolve trước mọi handler cũ và stage-only flags fail-safe.
- Definite Facebook failure được worker đưa qua official retry; mọi exception sau
  publish click được phân loại `FACEBOOK_PUBLISH_UNCERTAIN` và không auto-retry.
- Official dependency graph dùng `VerifiedWorkflowStages`; `CDHAPipeline` chỉ là
  alias tương thích, không được import bởi CLI/container/worker graph.
- Active source có đúng một `JobStatus` enum và một `JobStateTransitions` map.
- Final source state đạt 332 tests, compile, shell syntax, diff check và docs/static
  command audit; báo cáo đầy đủ nằm tại `docs/official-cli-state-machine-report.md`.

## Final Implementation Evidence

- `Settings` is the sole authoritative source; `FacebookBrowserConfig` is derived only.
- Worker, browser CLI, scripts, publisher, CDHA, downloader, and preflight resolve the same canonical configuration.
- Canonical profile: `runtime/chrome_profiles/cdha_automation`; canonical Reel cookie: `runtime/auth/facebook_cookies.txt`.
- Main CLI and browser CLI fingerprint: `db4dec838e17cb569e6a2fddec32f48a2501113ec133e0574747ef71ac906906`.
- Final verification: 345 passed, 0 failed, 0 skipped; no live browser or external side effect.
- Detailed evidence and migration map: `docs/unified-browser-configuration-report.md`.

# Findings & Decisions — Unified Automation Refactor

## Requirements

- Một entrypoint chính thức và một workflow có thẩm quyền.
- Clean Architecture thực dụng: Interfaces → Application → Domain; Infrastructure triển khai application ports.
- Trạng thái job bền vững, chuyển trạng thái hợp lệ, retry/resume và duplicate protection.
- Queue SQLite claim nguyên tử, lease/heartbeat, stale recovery.
- Một browser manager Playwright dùng chung và profile lock an toàn.
- Giữ nguyên hành vi đang chạy; legacy chỉ được cô lập sau characterization và tích hợp.
- Kiểm thử unit, integration và workflow; không publish Facebook thật.
- README và tài liệu cuối cùng phải phản ánh đúng source code.

## Research Findings

- Yêu cầu đầy đủ nằm trong attachment `pasted-text.txt`.
- Repository đã có `task_plan.md` từ một công việc tài liệu trước đó; nội dung cũ được giữ nguyên và kế hoạch refactor được thêm vào thay vì ghi đè.
- Sandbox cục bộ đang gặp lỗi khởi tạo loopback; thao tác đọc-only đã phải chạy theo đường phê duyệt.
- Worktree đã có một refactor lớn chưa commit trước phiên này: nhiều file gốc bị xóa và nội dung tương ứng xuất hiện dưới `app/infrastructure/legacy/`; nhiều package `app/application`, `app/domain`, `app/infrastructure`, worker và test mới chưa được track.
- Các thay đổi có sẵn chạm vào settings, browser clients, workflow, repository, adapters, dependencies và tests. Chúng phải được coi là công việc người dùng cần bảo toàn, không được reset hoặc ghi đè tùy tiện.
- Root hiện có cả `main.py`, `app/main.py`, `workers/main.py`, nhiều script worker/orchestrator và các standalone legacy entrypoint; chưa có một giao diện duy nhất rõ ràng.
- Tài liệu chạy hiện tại `HUONG_DAN_CHAY_DU_AN.md` mô tả hai luồng riêng: CLI pipeline và orchestrator + worker, cùng hai cơ chế cookie khác nhau. Đây là dấu hiệu phân mảnh cần đối chiếu với code.
- Legacy AutoFacebook README vẫn mô tả Selenium, profile phụ khi lock và anti-spam behavior; các điểm này xung đột trực tiếp với target (Playwright duy nhất, không silently tạo profile phụ) và cần được cô lập như tài liệu lịch sử.
- Crawler legacy có entrypoint `crawl_page.py` và Graph API/Selenium fallback riêng; cần xác định nó có thuộc workflow chính hay là tool read-only độc lập.
- Dependency chính hiện tại: Playwright, PyYAML, python-dotenv, filelock, pytest/pytest-asyncio, rich, yt-dlp và Pillow. Không thấy package Selenium trong `requirements.txt`, dù legacy vẫn chứa Selenium code.
- `.env.example` đã tập trung nhiều setting nhưng vẫn có hai cụm browser/profile/lock/queue (`CHROME_*` và `FACEBOOK_*`) cùng các biến legacy; cần truy vết setting nào thực sự được dùng.
- Baseline test `.venv/bin/pytest -q`: **260 passed, 5 failed** trong 6.48s.
- Ba failure ở `tests/test_facebook_page_state.py`: một page có `[role="navigation"]` bị phân loại `NETWORK_ERROR` thay vì `LOGGED_IN`.
- Hai failure ở `tests/test_ollama_output_parser.py`: formatter đang tạo `Label: value` trong khi contract test yêu cầu `Label:\nvalue`.
- Source inventory cho thấy hai kiến trúc active song song: pipeline cũ (`app/models`, `app/repositories`, `app/workflows`, `app/browser`) và Clean-Architecture Facebook jobs mới (`app/domain`, `app/application`, `app/infrastructure`, `workers`).
- Domain mới chưa độc lập: `app/domain/models/facebook_job.py` import `WorkflowStatus` từ legacy `app/models/workflow.py`.
- Có ít nhất ba persistence/queue implementation active: `app/repositories/job_repository.py`, `app/infrastructure/persistence/sqlite_job_queue.py`, và `app/browser/facebook_job.py` (bảng/database model riêng); adapter `sqlite_job_repository.py` chỉ bọc legacy repository và tự quyết định workflow transitions.
- Có hai worker active: `workers/facebook_browser_worker.py` dùng queue port/dispatcher, và `app/browser/facebook_browser_worker.py` dùng `FacebookJobStore` riêng; ngoài ra `scripts/run_end_to_end_worker.py` tự điều phối cả pipeline.
- Các file production vượt xa guideline 400 dòng: `facebook_client.py` 1030, `cdha_pipeline.py` 767, `cdha_client.py` 727, `app/main.py` 600, `downloadreel_adapter.py` 434, `job_repository.py` 429, `output_parser.py` 409.
- `CDHAPipeline` import trực tiếp concrete download/Facebook adapters, Chrome manager, resolver, settings và repository; đây là authoritative workflow hiện tại nhưng không phụ thuộc ports.
- SQL hiện nằm trong legacy `JobRepository`, queue mới, standalone `FacebookJobStore`, và preflight. Queue mới có `BEGIN IMMEDIATE`, attempt/max-attempt, next retry và events nhưng cần kiểm tra lease/heartbeat semantics chi tiết.
- Root `main.py` chỉ delegate sang `app.main.main`; đây là official CLI hiện tại nhưng dùng mutually-exclusive flags thay vì subcommands.
- `app.main` tự khởi tạo Settings, directories, logging và legacy JobRepository, rồi tạo concrete adapters/clients theo từng branch. Nó là composition root lẫn controller và chứa logic validation/retry orchestration.
- `workers.main` dựng một `DependencyContainer`; container tạo browser manager, CDP adapter, lock, three Facebook use cases, repository wrapper, queue và worker. Luồng này chỉ xử lý DOWNLOAD_REEL/CREATE_POST/JOIN_GROUP, không AI/CDHA/review.
- `scripts/run_end_to_end_worker.py` là orchestrator thứ hai: subclass `CDHAPipeline`, override private `_step_facebook`, tự queue download/post jobs, poll repository mỗi giây tối đa 600 giây, và gọi private `_step_ai`. Nó trộn hai kiến trúc và bypass official CLI.
- `scripts/add_job.py` hard-code `runtime/queue.db`, trong khi container dùng `FacebookBrowserConfig.queue_database_path`; đây có thể là hai queue path khác nhau.
- `CDHAPipeline.resume()` là state-router chính và nối download → Ollama → CDHA → screenshot → review → Facebook → permalink/comment → completed.
- Pipeline tự tạo concrete analyzers, CDHA/Facebook clients và adapters trong từng step. Human review được thực thi như interactive loop bên trong workflow service.
- Bug disconnected path: `run_until_review()` tham chiếu biến `adapter` chưa định nghĩa khi status là `FACEBOOK_PUBLISHING`.
- `_setup_signal_handlers()` dùng broad `except Exception: pass`; `_step_ai()` cũng catch broad Exception và phân loại tất cả thành `AI_FAILED`.
- `_completed_steps()` suy luận completion theo thứ tự khai báo enum thay vì persisted history, nên failure/retry states có thể tạo kết quả step không chính xác.
- `WorkflowStatus` đang trộn business workflow states với queue/worker states (`ACQUIRING_BROWSER_LOCK`, `RUNNING`, `RETRYABLE`, `BLOCKED`). State machine khai báo ba queue states không có outgoing transitions, trong khi queue tự ghi status string và không validate bằng state machine.
- Legacy `jobs` table chỉ có id/source/normalized URL/status/data JSON/timestamps; các trường attempt/max-attempt/claimed-by/lease/heartbeat/error/completed-at không phải columns có cấu trúc.
- Queue table có attempt/max-attempt/next-retry/error/timestamps và atomic `BEGIN IMMEDIATE` claim, nhưng **không có worker id, lease expiration hoặc heartbeat**. Startup recovery requeue mọi row đang RUNNING/ACQUIRING/WAITING, kể cả row có thể đang được worker khác xử lý.
- Queue port chỉ khai báo enqueue/dequeue/complete/fail; worker gọi `set_state`, `record_event`, `retry`, `recover_jobs` bằng `getattr`, khiến recovery semantics ẩn ngoài contract.
- Repository port quá hẹp và `SQLiteJobRepository.get_job()` là production stub luôn trả `None`; adapter còn tự quyết định trạng thái kế tiếp dựa trên current status, vi phạm repository boundary.
- Domain `FacebookJob` phụ thuộc legacy `WorkflowStatus`, dùng mutable payload dict và không có validation/idempotency key/lease metadata.
- Clean use cases bắt `Exception` rộng và biến mọi lỗi thành JobResult failure; không phân loại retryable/permanent. `DownloadReelUseCase` chứa URL regex và hard-code `runtime/downloads`, trùng trách nhiệm với legacy normalization/downloader.
- Queue và repository nằm ở hai SQLite path/schema độc lập, nên queue completion và authoritative workflow status có thể lệch nhau; `SQLiteJobRepository` cố bridge bằng implicit state decisions.
- Có hai typed config active: `Settings` và `FacebookBrowserConfig` cùng đọc overlap browser/profile/lock/timeouts từ `.env` + YAML. Official CLI dùng Settings rồi `ChromeManager` alias sang FacebookBrowserManager, manager lại load FacebookBrowserConfig riêng.
- `Settings.validate()` tồn tại nhưng `app.main.main()` không gọi; root path resolution đúng theo file location, song defaults vẫn chia runtime giữa `data/`, `logs/`, `screenshots/` và `runtime/`.
- `FacebookBrowserManager` là Playwright/CDP manager chính và không tạo secondary profile. Tuy nhiên `_acquire_profile_lock()` không được gọi trong `start()`/`ensure_chrome()`, nên official CLI không thực sự giữ manager profile lock.
- Worker có `FileBrowserLock` tốt hơn: owner token, PID/create-time/cmdline hash, heartbeat, stale archive, atomic metadata và context manager. Nhưng official CLI pipeline không dùng lock này; browser safety khác nhau theo entrypoint.
- `ChromeProcessManager` là một independent launcher khác, dùng `requests` (không có trong requirements) và broad exception; không thấy active import, nên là disconnected code.
- `scripts/start_facebook_browser.sh` và `stop_facebook_browser.sh` bypass manager/lock; stop script dùng broad `pkill -f remote-debugging-port=9222`, trái với scoped PID shutdown trong manager.
- `PlaywrightBrowserAdapter.get_page()` tạo page mới cho mọi logical name và không quản lý page lifecycle; `close()` không có trong BrowserPort.
- Browser code vẫn có nhiều fixed sleeps. Một số là bounded polling hợp lệ; các sleep đáng ngờ gồm Facebook publish `sleep(5)`, Gemini `sleep(0.5)`, old job client synchronous polling, và duplicate browser worker.
- `FacebookBrowserManager.save_diagnostics()` lưu HTML thô khi enabled, không áp dụng sanitizer như `FacebookStateDetector.save_unknown_artifacts()`; có nguy cơ lưu credential/PII.
- Hard-coded runtime paths còn trong clean `DownloadReelUseCase` và `PlaywrightReelAdapter`; adapter còn đọc root `Cookie.txt` và ghi `runtime/cookies.txt`.
- Clean Facebook adapters mới không bảo toàn hành vi: `PlaywrightGroupAdapter` chỉ navigate/close rồi trả `joined`/`published`; `extract_metadata()` trả fake `views: 100`; `share_post()` trả fake success; `PlaywrightPostAdapter` bỏ qua injected BrowserPort, tự load config/repository/ChromeManager và auto-confirm publish bằng lambda `"1"`.
- Các integration services mới xác nhận action chỉ bằng click/Enter rồi trả `submitted: True`, không verify permalink/external id/page state. Vì vậy chúng không thể là production authoritative path theo Definition of Done.
- Có duplicate `FacebookJobType`, duplicate `FacebookJob`, duplicate status enum, duplicate SQLite table `facebook_browser_jobs`, duplicate worker và duplicate CLI dưới `app/browser/*`. Stack này có idempotency key nhưng claim `next_pending()` không atomic và startup recovery requeue mọi RUNNING row.
- Duplicate browser worker catches mọi exception thành FAILED và không có retry classification; retry-waiting jobs được chọn ngay không có delay/lease.
- Legacy `DownloadReelAdapter` là working authoritative downloader: wrap `fb_downloader.process_and_download_reel` qua `asyncio.to_thread`, normalize URL/caption/comments, validate non-empty video + metadata sidecar, checksum, import history và persist transitions.
- Legacy downloader wrapper vẫn mutates `sys.path`, imports module name toàn cục và catches broad exceptions, nhưng đã có characterization/integration tests và duplicate protection mạnh hơn clean replacement.
- `FacebookPublisherAdapter`/`FacebookWebClient` là working authoritative Facebook path với preparation, manual gate, reconciliation, permalink extraction and comment verification. Discovered likely bug: adapter resolves `facebook_post_url` but passes `job.source_url` into `add_permalink_comment()`.
- Current AI authoritative path is Ollama (`provider_factory` → `OllamaAnalyzer`); Gemini browser client remains supported mainly as legacy/manual retry branch. CDHA authoritative path is `CDHAWebClient` with persisted stage transitions and result extraction.
- Deleted architecture docs from HEAD describe the pre-migration system intentionally as a local sequential single-worker workflow, while the new request requires durable queue/worker/orchestrator separation. Their safety contracts (human gates, no blind side-effect retry, privacy, uncertainty reconciliation) remain valid and must be preserved.
- Existing HEAD docs claim 224/224 tests in the prior architecture; current expanded worktree has 265 tests after new queue/browser changes, with baseline regressions now fixed.
- Uncommitted migration moves both mini-project trees under `app/infrastructure/legacy/` but Git records this as deletes + untracked copies because nothing is staged. Historical architecture/security/operations docs were deleted rather than migrated into `docs/`.
- `.gitignore` protects secrets, DB/media/profile/data/log roots, but does not ignore all of `runtime/`; lock guard/startup files, PID files, JSON/HTML diagnostics and stale profile marker symlinks can remain untracked candidates.
- One tracked runtime artifact existed in HEAD: `dowloadReelFB/activity_log.json`; its moved legacy copy remains in working tree. It contains history/state and should be treated as migration data, not normal source.
- Runtime contains real browser profiles, cookies, downloaded videos, queue DB, PID/lock metadata. No runtime data may be deleted or rewritten during refactor.
- Run guide accurately exposes fragmentation: two cookie inputs, two databases, multiple artifact/diagnostic roots, official CLI plus two-terminal orchestrator/worker plus manual browser scripts.
- Static AST analysis found no direct production import cycles and no Python syntax errors.
- Disconnected/zero-indegree modules include unused use cases (`check_facebook_login`, `extract_reel_metadata`, `share_facebook_post`), `facebook_job_client`, `process_file_lock`, domain exception/model modules, and `ChromeProcessManager`.
- `.env` exists locally and was intentionally not read. Filename-only secret scan found expected config/logging/cookie handling files; no secret value was printed.
- `runtime/cookies.txt` and `runtime/queue.db` are ignored, but lock guard, PID and diagnostics JSON are not ignored by current patterns.
- Compile baseline passes for `app`, `workers`, `config`, `scripts`; pytest collection succeeds with **265 tests** including 53 crawler characterization tests now located under legacy.

## Technical Decisions

| Decision | Rationale |
|---|---|
| Audit và test baseline trước mọi thay đổi hành vi | Đây là ràng buộc bắt buộc và giúp bảo vệ các tính năng legacy đang chạy |
| Giữ lịch sử kế hoạch cũ trong `task_plan.md` | Tránh ghi đè công việc người dùng chưa commit |
| Facebook state hypothesis: indicator set thiếu selector generic `[role="navigation"]` | Ba test đều cung cấp đúng selector này; detector chỉ probe các descendant cụ thể nên tập `visible` không có `logged_in`, body rỗng và bị fallback thành `NETWORK_ERROR` |
| Ollama formatter hypothesis: recent worktree edit đổi contract từ `label:\nvalue`/blank-line separation sang `label: value` | `git show HEAD` và test mới đều xác nhận format cũ là contract; phần filtering placeholder mới có thể giữ lại độc lập |

## Issues Encountered

| Issue | Resolution |
|---|---|
| `bwrap: loopback: Failed RTM_NEWADDR` | Dùng quyền execution read-only ngoài sandbox khi cần |
| Baseline có 5 test failure trước refactor hành vi | Áp dụng systematic debugging; chưa sửa cho đến khi xác định root cause và recent diff |
| Runtime lock/PID/diagnostic metadata not fully ignored | Plan to ignore entire `runtime/` while preserving existing local data |

## Debugging Evidence

- Facebook failure tái hiện ổn định trong full suite. `_INDICATORS` không chứa selector mà fixture đánh dấu visible; `_classify()` vì thế đi đến nhánh `not text.strip()` và trả `NETWORK_ERROR`.
- Ollama failure tái hiện ổn định. Diff cho thấy chính thay đổi chưa commit tại `_dict_to_cf_text()` đã thay newline format; phiên bản HEAD dùng đúng format mà tests yêu cầu.
- Existing failing tests đã là minimal reproductions, nên không cần tạo test trùng lặp trước fix.
- Facebook hypothesis được xác nhận: thêm đúng một generic navigation indicator làm toàn bộ 13 test page-state pass.
- Ollama hypothesis được xác nhận: khôi phục `label:\nvalue` và blank-line separation làm 53 parser/downstream contract tests pass; filtering placeholder mới vẫn được giữ.

## Resources

- Yêu cầu: `/home/nguyen-son-minh/.codex/attachments/fb5668a7-c740-421b-9db4-8d5894eaf305/pasted-text.txt`
- Kế hoạch: `task_plan.md`
- Nhật ký: `progress.md`

## Pre-refactor Report

Hoàn tất tại `docs/pre-refactoring-report.md`. Báo cáo đóng băng baseline 265 tests, liệt kê entrypoint/module/dependency problems và phân loại workflow thực tế trước structural edits.

## Final Decisions and Evidence

- Official path is `main.py` → `app.main` → `app.bootstrap` → application use cases → ports/domain → infrastructure.
- `ProcessJobUseCase` is the sole active state-aware roadmap; the characterized pipeline runs one injected external stage per call.
- Official workflow jobs and queue work items share `DATABASE_PATH` with separate tables/events.
- Queue startup recovery is lease-aware and will not steal a fresh claim; owner-only heartbeat and atomic two-worker claim are tested.
- `FacebookBrowserManager` is the only active Chrome/CDP launcher; alternate launcher and old browser worker/store are isolated under `app/infrastructure/legacy/`.
- Medical review and Facebook publish remain separate manual gates. Queue migration never converts an old `CREATE_POST` row into publish authorization.
- Final automated evidence: **280 passed in 6.48s**, compileall pass, shell syntax pass, and `git diff --check` pass.
- Remaining manual-only acceptance: live site selector drift/auth challenges, real CDHA semantic correctness, and real Facebook publish/permalink verification. Automated tests intentionally do not perform those side effects.

## CDHA Clinical Summary Debugging — Initial Trace

- Yêu cầu mới dùng Reel `https://www.facebook.com/reel/1569069054789810` và yêu cầu real E2E ở configured test mode; production manual review phải giữ nguyên.
- Attachment kết thúc đột ngột tại dòng 216 giữa code fence sau trường `raw_impression`; các yêu cầu extraction/validation/workflow trước đó vẫn đủ rõ để triển khai an toàn.
- Active data path được tìm thấy: `CDHAWebClient._extract_result` → `CDHAResult` (`app.models.results`) → persisted `cdha_result` trong pipeline/repository → `FacebookPublisherAdapter.prepare` → `PostContentService.build_post` → `FacebookWebClient`.
- `selectors.yaml` hiện có fallback `text: "Key findings:"` và `text: "Impression:"`; đây là candidate có khả năng trả chính heading thay vì container/value khi DOM thật lồng nội dung.
- `PostContentService` đã chặn list rỗng và impression rỗng, nhưng chưa thấy bằng chứng nó chặn label-only values (`"Key findings:"`, `"Impression:"`) hoặc bắt buộc exact CDHA result URL.
- `ReviewService` hiển thị `key_findings`/`impression`, còn auto-approve hiện chỉ dựa trên setting chung; cần xác minh validation có chạy trước approval và ngay trước publish.
- Existing fixture dùng `[data-key="cdha.key_findings"]` với text phẳng, nên chưa tái hiện nested real DOM/heading leakage.
- Extractor root-cause evidence: `_optional_text()` calls `resolver.find_first()` and returns `locator.inner_text()` for whichever candidate wins. `_field()` accepts every non-empty string without distinguishing a heading label from a value.
- `_split_lines()` only strips bullets/whitespace. Therefore a matched heading `Key findings:` becomes the valid one-item list `["Key findings:"]`, while `Impression:` remains a truthy impression. This bypasses current empty-only validation exactly as reported.
- `CDHAAnalysisResult` has no `analysis_url`, source language, or raw per-field values. The pipeline consequently cannot prove that Facebook content came from an exact CDHA result URL or retain raw-vs-normalized extraction evidence.
- Selector behavior confirms the hypothesis: CSS test-id is tried first, then broad non-exact `get_by_text()` heading candidates. `find_first()` always takes `.first`, so a visible heading is a successful extraction and no parent/sibling/value traversal occurs.
- The same label-as-value risk exists for triage, confidence, detailed analysis, and marked regions. The publish defect is specifically observable for findings/impression because those fields are consumed by the Facebook formatter.
- Downstream confirmation: `PostContentService.build_post()` rejects only empty findings/impression, so label-only values pass. `validate_post_text()` checks PII, local paths, and credential tokens but does not re-parse/validate required clinical sections.
- `FacebookPublisherAdapter.prepare()` passes persisted `cdha_result` directly into the formatter. `publish()` delegates without re-validating the persisted/prepared text, so a stale or altered invalid caption can reach the click boundary.
- The formatter currently appends `&ref=CD2ED52966` unconditionally to `cdha_view_url`; empty input produces an invalid analysis source and existing query strings are not handled safely. Hashtags are hard-coded rather than configured.
- `ReviewService.review()` auto-approves whenever `AUTO_APPROVE_REVIEW` is true, before any clinical-summary validation. Manual choice `1` also validates only privacy text, not findings/impression/source URL integrity.
- Workflow transitions `SCREENSHOTS_CAPTURED → WAITING_FOR_REVIEW` unconditionally after a successful CDHA call; no structured clinical-summary validation occurs at that boundary.
- CDHA persistence writes the extracted DTO and exact `page.url` separately as `cdha_view_url`; the DTO JSON itself loses URL/raw-field provenance. The same unvalidated result is persisted twice (before and after screenshots) and then transitions to review.
- Final publish click boundary reads persisted `facebook_post_text` and, after operator choice, performs only a privacy scan. It never invokes `validate_post_text()` for the current persisted text immediately before `FACEBOOK_PUBLISHING`/button click.
- Root cause is now established across boundaries: broad heading selector + truthy-only extraction + empty-only formatter validation + no review/publish structured guard. A label becomes a valid value at the extractor and survives every later layer.
- Existing CDHA extraction test supplies already-flattened strings through `KeyResolver`; it cannot exercise heading-vs-container DOM behavior or nested text nodes. Existing formatter tests cover only empty values, not label leakage, required URL provenance, unsafe claims, or exact section structure.
- Settings already distinguish `TEST_MODE`, `FACEBOOK_TEST_TARGET_URL`, production-target override, test-mode comment behavior, and `AUTO_APPROVE_REVIEW` (default false). There is no configured hashtag field or E2E-specific validated-auto-review switch yet.
- Existing Facebook tests intentionally prepare/publish arbitrary short text to characterize browser behavior. The new strict clinical validator should therefore be a distinct `validate_publish_ready` contract invoked by generated-post construction, review, and the final click boundary; generic composer text validation can remain reusable.
- `approved_job()` fixtures contain valid Vietnamese CDHA values but omit `cdha_view_url`; tests can be upgraded with a synthetic exact result URL so the final guard exercises the intended production contract.
- `.env.example` documents test mode separately but currently lacks configurable hashtags. Adding `FACEBOOK_POST_HASHTAGS` is additive and preserves the existing hashtag set as default.
- PrivacyService can enforce the requested text PII categories without storing matched values. URLs must be removed from the scan input before validation, as current post validation already does.
- `.env.example` advertises `TEST_MODE_POST_PREFIX` and `TEST_MODE_REQUIRE_SYNTHETIC_CASE`, but typed `Settings` does not load them and no production code references them. Test-mode target routing exists only in `CDHAPipeline`; this gap must be considered before any real external publish attempt.
- Confirmed E2E routing defect: `_step_facebook()` accepts a configured `FACEBOOK_TEST_TARGET_URL`, but `FacebookPublisherAdapter.prepare()` then rejects an empty production target and always passes `FACEBOOK_TARGET_URL` to the client. Thus configured test mode is not actually authoritative at the side-effect boundary.
- Preflight checks that a test target exists but does not verify target separation/override policy. `TEST_MODE_DISABLE_COMMENT` is loaded but unused. Both must be enforced before the requested real E2E run.
- Existing review fixtures do not persist `cdha_result` or `cdha_view_url`; enforcing the new review contract requires upgrading them to reflect the real `WAITING_FOR_REVIEW` invariant and adding a label-only rejection case.

## Implemented Clinical Summary Contract

- `CDHAClinicalSummary` preserves raw findings/impression, normalizes labels/bullets, requires non-label content and an exact HTTPS analysis URL, and serializes structured provenance.
- CDHA extraction now reads contextual sibling/parent content when a selector resolves a heading and adds stable `data-key`/`data-field` selectors. Label-only output fails before `CDHA_ANALYZED`.
- Review approval validates non-empty Vietnamese summary content, PII, absolute claims, and analysis URL. Auto-approval uses the same validator; default production behavior remains manual.
- Generated posts use exact source/result URLs, configured hashtags, cautious validated source content, safe decimal display normalization, and the required professional disclaimer.
- The final Facebook click boundary revalidates persisted CDHA values against the exact prepared caption. Stale/edited label-only content cannot advance to `FACEBOOK_PUBLISHING`.
- Test mode now routes to `FACEBOOK_TEST_TARGET_URL`, rejects accidental production-target equality unless explicitly overridden, and suppresses permalink comments when configured.

## Real E2E Evidence and Downloader Timeout

- Job `1aab9d248a1b46338d592754d53011d8` was created for the requested Reel and claimed by the official worker.
- Real media download completed successfully (video/audio totaling about 13.75 MB) and yt-dlp output exposed the actual Baker's cyst/knee osteoarthritis caption.
- The authoritative job nevertheless transitioned `DOWNLOADREEL_RUNNING → DOWNLOADREEL_FAILED` after 180 seconds with legacy child job `a0b289b5cf044c2f9cf39cfa82de76db` unfinished.
- Root-cause trace: legacy `ReelScraper.scrape()` submits `EXTRACT_REEL_METADATA` and `EXTRACT_COMMENTS` to the isolated `facebook_browser_jobs` queue through `FacebookJobClient.wait()`. The official worker only dispatches `PROCESS_WORKFLOW`, so no active worker can complete that child record. The successful downloader blocks and is discarded.
- This is a real integration seam left by the legacy downloader wrapper, not a network/download failure. Fix must remove the inactive child-queue dependency from the official downloader path and retain caption/comment data from verified local/downloader artifacts.
- `process_and_download_reel()` constructs `ReelScraper` before download and unconditionally calls it after `download_single()` succeeds, so the child-queue timeout prevents writing the metadata sidecar and returning the otherwise valid `VideoInfo`.
- yt-dlp already populates the downloaded `VideoInfo.title` with the real caption-like Reel description shown in logs. The result model currently lacks a dedicated description/comments field; the safe fallback is to persist this verified title as caption and an explicit empty comments list, rather than fabricate comments or wait on an inactive queue.
- Minimal compatibility-preserving hypothesis: add an opt-out parameter to legacy `process_and_download_reel()` (default keeps standalone behavior), and have only `DownloadReelAdapter._get_downloader()` disable child-queue scraping. In that official mode, write a verified sidecar from `VideoInfo.title`, empty comments, and an explicit metadata extraction status.
- Retry confirmed the hypothesis: official download completed in ~4 seconds, persisted a 14,421,435-byte video, caption, empty verified-comments list, sidecar and checksum; it no longer touched the isolated child queue.
- Real Ollama completed in `VISION_FRAMES` mode with low confidence and one warning, then CDHA uploaded/analyzed successfully at exact URL `https://cdha.ai/dash?view=44081`.
- Live extraction preserved raw label-prefixed fields but normalized them into four Vietnamese findings and one Vietnamese impression. This is direct runtime evidence that nested/heading extraction now returns values, not labels.
- Two required screenshots were captured and the job reached `WAITING_FOR_REVIEW`. Process-local `AUTO_APPROVE_REVIEW=true` passed the strict summary validator and moved it to `APPROVED`; `.env` production default was not changed.
- Non-sensitive config audit confirms `TEST_MODE=false`, no `FACEBOOK_TEST_TARGET_URL`, and no production override. Therefore no configured E2E publish destination exists; the queued Facebook stage must not be allowed to target `/me` under a fabricated test configuration.
- Expected red test evidence: focused collection fails because `app.domain.models.cdha_clinical_summary` does not exist yet. This is the intended pre-implementation failure for the new structured contract.
