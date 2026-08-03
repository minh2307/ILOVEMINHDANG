# Findings — Prompt 5 Facebook Publication Verification

- Ngày 2026-08-03, `promt.md` hiện có **1.258 dòng**, SHA-256
  `2011198ec0be33b54c9cdff0a0e59a51bc1a4fab7bf0accd1c5cef0f05859195`;
  đây là Prompt 5, không phải Prompt 4 được ghi trong chat tham chiếu.
- Baseline được prompt tuyên bố là 388 test; cần chạy lại, không dựa riêng vào
  lịch sử.
- Definition of Done yêu cầu verified post ID/permalink, mandatory validation,
  durable attempt trước side effect, `SUBMITTING` trước final click, uncertain
  state không auto-retry, reconciliation use case/CLI và duplicate prevention
  qua restart.
- Live publication không được thực hiện hoặc tuyên bố nếu không có readiness,
  authorization và bằng chứng post thật. Mock chỉ chứng minh automated behavior.
- Worktree hiện chứa thay đổi Prompt 1–4 (28 file trong diff stat cùng file mới);
  toàn bộ được bảo toàn, không reset/ghi đè.
- Chưa sửa production cho Prompt 5; đang ở systematic-debugging Phase 1:
  persisted evidence và official call graph trước mọi fix.
- Đã đọc trọn 1.258 dòng Prompt 5. Các ranh giới bắt buộc gồm semantic composer
  selectors, kiểm tra caption/media ngay trong composer, exact post evidence,
  canonical permalink validation, attempt lifecycle, retry-safety taxonomy,
  reconciliation và sanitized job-specific diagnostics.
- Safe verification chỉ được dùng fixture/mock nếu live flag không bật; Full
  chỉ chạy khi có quyền live. Không tự bật `E2E_ALLOW_LIVE_PUBLISH`.
- Active official flow sơ bộ: `app.main confirm-publish` →
  `ConfirmPublishUseCase` → durable queue → `ProcessJobUseCase`/
  `VerifiedWorkflowStageAdapter` → `CDHAPipeline` →
  `PlaywrightFacebookAdapter` → `FacebookWebClient`.
- `FacebookWebClient` hiện đã có prepare/publish/reconcile/permalink helpers và
  trạng thái `FACEBOOK_PUBLISH_UNCERTAIN`, nhưng publish đang chuyển thẳng
  `FACEBOOK_PUBLISHING → FACEBOOK_PUBLISHED` trước stage extract permalink.
  Đây là gap cần chứng minh bằng đọc source/test/persisted records.
- Có một `PlaywrightPostAdapter` khác cho legacy port; cần xác định nó chỉ
  delegate/deprecate hay còn active trước khi kết luận có hai publisher.
- `confirm-publish` hiện chỉ hỏi exact phrase rồi queue một workflow item; Worker
  chạy `ProcessJobUseCase`. Khi status `FACEBOOK_PUBLISHING`, use case gọi
  reconciliation; khi `FACEBOOK_PUBLISHED`, nó tách permalink thành stage sau.
- `PlaywrightPostAdapter` đã bị vô hiệu hóa rõ ràng và không tạo browser/publish;
  active publisher là `FacebookPublisherAdapter`/`FacebookWebClient`.
- Active adapter xây caption và ghi cả raw `facebook_post_text` vào job data,
  rồi gọi browser prepare. Nó chưa có mandatory domain validation/attempt
  persistence boundary riêng trước browser side effect.
- `FacebookPublisherAdapter.complete()` vẫn coi `FACEBOOK_PUBLISHED` là đầu vào
  để trích permalink, xác nhận evidence được persist sau published state ở thiết
  kế hiện tại.
- Root cause false success đã xác nhận trực tiếp:
  `publication_is_verified()` cho phép `publish_success` toast +
  `composer_closed` thành verified dù không có exact candidate. Result có thể
  `success=True` với cả `post_id=None` và `post_url=None`, rồi transition
  `FACEBOOK_PUBLISHED`.
- Publisher chỉ lưu `FACEBOOK_PUBLISHING` trước click; chưa có durable
  publication-attempt entity/status `SUBMITTING`/`SUBMITTED_UNCONFIRMED`.
  `publish_clicked` chỉ là biến RAM, nên crash boundary không bền vững.
- Hai-step flow nuốt mọi exception khi tìm/click nút `post_button`, làm mờ
  không tìm thấy/hidden/disabled/ambiguous và có thể tiếp tục verification sai.
- Selector `publish_button` trộn `Tiếp/Next` và `Đăng/Post` cùng broad CSS
  `:has-text`, trong khi Prompt 5 cần phân biệt action và chặn ambiguity.
- Validation hiện có nền tảng tốt (clinical sections, URLs, PII, images) nhưng
  fingerprint chỉ gồm target+caption+media; thiếu job/source/CDHA external ID.
  Nó cũng chưa xác nhận caption hiện tại khớp approved snapshot/version.
- `_ensure_authenticated()` chuyển UNKNOWN thành `RETRYABLE` ở prepare; Prompt 5
  yêu cầu UNKNOWN block publication/reconciliation, không xem là selector drift
  hoặc auto-retry.
- `FACEBOOK_PUBLISH_UNCERTAIN` hiện terminal và không thể completed (đúng safety
  nền), nhưng không có đường official reconciliation success từ trạng thái đó.
- Canonical persisted DB là `data/jobs.sqlite3`; schema hiện không có bảng
  publication attempts. Attempts chỉ tồn tại gián tiếp trong `jobs.data_json`
  và `job_events`, không có unique fingerprint/status ownership riêng.
- Persisted evidence:
  - job `cf768...` đang `COMPLETED` nhưng vẫn có
    `facebook_publication_uncertain=1`, `post_id=NULL` và một share URL;
  - job `50e5...` ghi nhận nhiều UNKNOWN → `RETRYABLE`, sau đó nhiều lần
    `FACEBOOK_PUBLISHING → FACEBOOK_PUBLISH_UNCERTAIN → FAILED`;
  - event 467 ghi đúng lỗi `FACEBOOK_PUBLISH_FAILED -> COMPLETED`;
  - các event cũ có uncertain bị đưa trở lại manual review/post URL extracted,
    chứng minh duplicate/bypass risk trong persisted history;
  - nhiều failure do selector chọn nhầm nút `Tiếp` disabled hoặc không resolve
    composer/publish controls.
- Có ít nhất một record `FACEBOOK_PUBLISHING → FACEBOOK_PUBLISHED` rồi mới
  `POST_URL_EXTRACTING`, phù hợp với false-success architecture đã thấy trong
  source; DB có verified records nhưng evidence model không tách raw/canonical
  hoặc attempt.
- Baseline trực tiếp ngoài restricted sandbox: **388 passed in 5.28s**. Suite
  sandbox treo ở ~27% vì stream-fd infrastructure, không phải test failure.

---

# Findings — Prompt 4 CDHA/Browser Reliability

- Ngày 2026-08-03, `promt.md` hiện có 945 dòng và là Prompt 4, không phải Prompt
  3 hay refactor browser cũ trong chat tham chiếu.
- Phạm vi chính: failure evidence thật, browser ownership/health, CDHA selector
  và semantic state, bounded waits/timeouts, queue lease/heartbeat/recovery,
  retry/idempotency và diagnostics sanitized.
- Baseline worktree có 15 modified/untracked files thuộc Prompt 3; tất cả được
  coi là thay đổi người dùng cần bảo toàn. Không reset hoặc ghi đè.
- Planning files hiện chứa lịch sử nhiều prompt; kế hoạch Prompt 4 được thêm ở
  đầu thay vì xóa lịch sử.
- Chưa có kết luận root cause hoặc thay đổi production nào cho Prompt 4.
- Đã đọc trọn 945 dòng prompt. Definition of Done yêu cầu không chỉ selector:
  phải chứng minh ownership, health states, bounded semantic waits, typed
  timeouts, lease/heartbeat, crash resume, CDHA idempotency, auth taxonomy,
  diagnostics, official recovery commands và full regression suite.
- Source hiện đã có nền tảng lease/heartbeat: `workers/facebook_browser_worker.py`
  nhận queue lease/heartbeat, tạo task renew lease, duy trì browser-lock
  heartbeat và giới hạn retry; SQLite queue có `claimed_by`,
  `lease_expires_at`, `last_heartbeat`.
- `workers/facebook_browser_worker.py` vẫn có mặc định lock wait 180 giây; settings
  cũng còn nhiều timeout 180 giây. Cần phân biệt hard-coded legacy với timeout
  có kiểu trước khi kết luận lỗi.
- Selector CDHA hiện có `#btnComplete` trong `app/config/selectors.yaml`; chưa xác
  nhận registry/detector active hay fallback thực tế vì output search bị
  truncate.
- Các fixed waits thấy trong active `screenshot_service.py` và nhiều module
  legacy. Phần `.disabled`/`infrastructure/legacy` không được coi là active nếu
  call graph không dẫn tới chúng.
- Runtime hiện có Full/Quick preflight diagnostics, Facebook debug HTML/metadata,
  Chrome PID và browser lock guard/startup. Chưa đọc nội dung nhạy cảm; mọi lần
  đọc tiếp sẽ chỉ lấy metadata/sanitized evidence cần thiết.
- Có nhiều SQLite ứng viên (`data/jobs.db`, `data/cdha_workflow.db`,
  `data/jobs.sqlite3`, `data/cdha_jobs.db`, `data/facebook_browser_jobs.sqlite3`,
  `data/pipeline.db`, `runtime/jobs.sqlite3`, `runtime/queue.db`). Cần xác định
  canonical path từ `Settings`/composition root trước khi query record.
- Active `CDHAWebClient.analyze_video()` mở trang qua `ChromeManager.new_page()`,
  dùng `cdha_view_url` để bỏ upload khi đã có result URL, nhưng đường no-result
  upload/chạy analysis chưa thấy external ID/fingerprint trong phần đã đọc.
- Client hiện gọi `_wait_for_analysis(page)` sau click và có một fixed
  `page.wait_for_timeout(1000)` ở auto-share; auto-share nằm trước transition
  `CDHA_ANALYZED` và nuốt mọi exception thành warning. Cần đọc toàn bộ helper/call
  chain để xác định tác động, chưa sửa.
- `FacebookBrowserManager.close()` chỉ dừng Playwright connection và release lock,
  không gọi `browser.close()`/`context.close()`. `new_page()` trả page trực tiếp;
  ownership/release API và health taxonomy chưa thấy trong phần source đã đọc.
- `SQLiteJobQueue.dequeue()` claim bằng `BEGIN IMMEDIATE` + conditional update,
  heartbeat chỉ gia hạn khi đúng worker và trạng thái in-flight. `recover_jobs()`
  chỉ xét lease expiry, tăng attempt rồi requeue; chưa thấy enforcement
  `max_attempts` trong recovery hoặc stage field riêng.
- Composition root xác nhận canonical workflow DB/queue cùng dùng
  `Settings.database_path`, mặc định `data/jobs.sqlite3`.
- Settings hiện tách `page_timeout=60`, `upload_timeout=180`,
  `cdha_analysis_timeout=900`, poll/stability, browser startup/lock wait,
  queue lease/heartbeat và Facebook timeouts. Tuy vậy chưa có CDHA result timeout,
  worker stage timeout hay browser action/navigation names độc lập đúng contract.
- `CDHAWebClient._complete_upload()` resolve `cdha.upload_complete_button` trong
  iframe với timeout 90 giây, chỉ kiểm `is_enabled`, rồi click; hidden/attached/
  covered/page-health/current semantic state chưa được tách.
- `_wait_for_analysis()` đã dùng monotonic bounded polling, nhưng trả success chỉ
  theo `view=` URL hoặc generic `analysis_complete`/result selector và ném
  `TimeoutError` chuỗi; chưa có final structured state, stage-aware diagnostics
  hay callback heartbeat.
- Host thiếu CLI `sqlite3`; đây là tooling môi trường, không phải code defect.
  Query tiếp theo sẽ dùng Python stdlib ở chế độ read-only.

## Persisted Failure Inventory

| Failure | Evidence source | Stage | Current handler | Observed retry |
|---|---|---|---|---|
| Page/context/browser closed during analysis | `job_events` 53, job `cf768...`: `Page.title: Target page, context or browser has been closed`; workflow log lines 578–609 | `CDHA_ANALYZING` | generic Playwright mapper; diagnostic screenshot/title retried on closed page | transitioned `CDHA_FAILED`; historical repeats |
| Browser connection closed before page acquisition | `job_events` 272, job `e6f...`: `BrowserContext.new_page: Connection closed while reading from the driver` | `CDHA_OPENING` | `BROWSER_NETWORK_ERROR`, retryable | no health-specific persisted state |
| `#btnComplete` hidden and disabled | `job_events` 238: locator repeatedly resolved to hidden `<button disabled id="btnComplete"...>` then target closed | `CDHA_UPLOADING` / selector resolution | `SelectorResolver.find_first()` waits for visible, ultimately mapped `BROWSER_TARGET_CLOSED` | retryable target-closed obscures hidden/disabled state |
| Completion control missing | `job_events` 234 + log 2115: only `#btnComplete`, timeout/`SELECTOR_NOT_FOUND` | `CDHA_UPLOADING` | selector resolution error | non-retryable/manual |
| Upload unacknowledged | events 39/226 and log 437: upload start not detected/outcome uncertain | `CDHA_UPLOADING` | `CDHAUploadError(CDHA_UPLOAD_UNCERTAIN)` | non-retryable/manual |
| CDHA login required | events 20/444; diagnostic title localized login | `CDHA_OPENING` | transition `NEEDS_CDHA_LOGIN` | manual action, no bypass |
| Result URL treated complete with unknown job logging | log 2158/2500/2623/2676/2904 | `CDHA_ANALYZING` | `_wait_for_analysis` returns on any `view=` URL and logs `job_id=unknown` | proceeds as success before result extraction validates |
| Queue item timeout near 180s | queue row `...:CREATED` and event 516: worker did not complete inner job within 180s | `DOWNLOADREEL_RUNNING` | legacy worker-client wait | failed; later separate retry queue item completed |
| Facebook auth/unknown | events/logs 485+, log 2870–2884 | `FACEBOOK_PREPARING` | page-state detector distinguishes `login_required` and `unknown` | publish blocked/failed; Prompt 5 owns verification |

- Canonical DB contains 11 workflow jobs, 529 job events, 3 official queue rows
  and 13 queue events. No active lease/heartbeat values remain in persisted rows.
- Diagnostics are currently sparse two-field JSON for CDHA failure
  (`url`, masked `title`); selector diagnostics do record candidates/failures and
  workflow context. They do not yet record browser health, semantic CDHA state,
  timeout, lease or heartbeat.
- The historical hidden/disabled evidence proves the active UI really used an
  iframe `#iframeContent` and `#btnComplete`, so keeping that ID as a registry
  candidate is justified; inventing `data-testid` would not be.
- The diagnostic failure path is a confirmed secondary root cause: after a
  TargetClosed error, `save_diagnostics()` unconditionally screenshots and reads
  title/content, causing another TargetClosed and losing the intended bundle.
- `SelectorResolver.find_first()` supports ordered candidates but only returns a
  locator once visible. It cannot report attached/hidden/disabled separately;
  `exists()` also means “visible”, not DOM presence. This is the source-level
  reason persisted hidden `#btnComplete` becomes a timeout/selector/closed error.
- `selectors.yaml` has exactly one upload completion candidate:
  `css: #btnComplete`. Actual evidence supports adding real semantic fallback
  `role=button` with localized text `Hoàn tất` (seen in persisted DOM trace);
  no evidence yet for an iframe alternative or `data-testid`.
- Canonical errors collapse page/context/browser closure into one
  `BrowserTargetClosedError`. Browser port/model are skeletal and disconnected
  from the active `FacebookBrowserManager`; they expose no acquire/release/health.
- Worker creates queue-heartbeat immediately after claim and cancels it in
  `finally`, while browser lock heartbeat runs only after acquisition. It records
  no workflow stage into queue, does not interrupt dispatch after lease renewal
  rejection, and classifies retryability by string matching instead of the
  canonical `PipelineError.retryable`.
- Existing tests already cover atomic claim, fresh-heartbeat recovery protection,
  lock timeout, one bounded upload acknowledgement, one CDHA analysis timeout
  and target-closed not becoming selector-not-found. They do not cover hidden/
  disabled control taxonomy, health distinction, lease-loss cancellation,
  max-attempt recovery or idempotent external submission.
- Lần probe trước dùng các `test -x` nối bằng `;` nên output cuối không chứng minh
  từng đường dẫn tồn tại. Baseline xác nhận `/tmp/minhdang-preflight-venv` đã bị
  dọn; repository `.venv` cần được kiểm tra riêng và không được sửa.
- Baseline thực tế của worktree hiện tại là **357 passed in 5.51s**, không phải
  356; chênh một test đến từ Prompt 3 đang chưa commit. Restricted sandbox dừng
  giữa suite không có summary, còn cùng lệnh ngoài sandbox hoàn tất exit 0.
- `cdha_external_analysis_id` chỉ xuất hiện trong một lifecycle unit test; active
  `CDHAWebClient` hiện chỉ reuse `cdha_view_url`. Vì vậy test resume ở application
  layer tạo cảm giác idempotency nhưng browser adapter chưa persist/reconcile
  external identity đúng lúc.
- Download adapter đã persist `checksum_sha256`; đây là stable source-video hash
  có thể được đưa vào CDHA submission fingerprint cùng `job_id` và normalized
  source URL, không cần hash lại filename.
- `FacebookTabManager` đã có ownership per job và chỉ close temporary Facebook
  pages trong `release_job`; `CDHAWebClient` không dùng tab manager và không
  release page nó tạo, nên temporary CDHA pages hiện leak cho đến manager detach.
- Official CLI đã có `status/retry/resume` use cases. Chưa có `inspect-browser`
  hay `inspect-queue`; `status` trả queue rows liên quan nhưng không có diagnosis/
  next-action synthesis.
- `sanitized_runtime_configuration()` chỉ xuất browser startup/action timeout;
  chưa xuất upload/analysis/result/lease/heartbeat/stage timeouts theo Prompt 4.
- Audit path của transition map bị đoán sai; phải locate bằng source search, chưa
  có kết luận về trạng thái auth blocked mới.
- Transition map thật ở `app/domain/rules/state_transitions.py`. `NEEDS_CDHA_LOGIN`
  hiện có thể đi `CDHA_UPLOADING/CDHA_FAILED/RETRY_PENDING`; không có
  `WAITING_FOR_AUTH_REVIEW` từ CDHA. Giữ `NEEDS_CDHA_LOGIN` là manual blocked
  state phù hợp hơn thêm enum thứ hai.
- CDHA contract test hiện giả Chrome `wait_for_manual_action()` tự hoàn tất và
  chỉ xác nhận final `NEEDS_CDHA_LOGIN` khi auth vẫn thiếu. Có thể bỏ interactive
  wait khỏi worker adapter và trả manual error ngay mà vẫn giữ state semantics;
  login setup CLI riêng vẫn là nơi hợp lệ để chờ operator.
- Root-cause hypotheses đã đủ cụ thể để bắt đầu test đỏ:
  1) visible-only resolver gây mất hidden/disabled state;
  2) manager thiếu tracked page ownership/health;
  3) CDHA chỉ reuse result URL, không persist submitting fingerprint/external ID;
  4) queue recovery không enforce max attempt và worker không dừng khi lease mất;
  5) closed-page diagnostics cố tương tác với target đã chết.
- Regression Prompt 4 ban đầu đỏ đúng tại import `app.browser.cdha_state`, chứng
  minh test chưa thể pass nhờ implementation cũ. Sau implementation đầu,
  **13/13 Prompt 4 tests** và **86/86 focused existing+new tests** pass.
- Lifecycle ownership cuối pha 2: `FacebookBrowserManager` sở hữu Playwright
  attach, shared connection/context/lock và registry temporary pages; adapter chỉ
  release page manager đã cấp. `close()` release tracked pages rồi chỉ detach
  Playwright, không close shared browser/context.
- Health state hiện phân biệt `DISCONNECTED`, `CONTEXT_CLOSED`, `PAGE_CLOSED`,
  `CONNECTED`; errors riêng không còn buộc mọi closure thành selector mismatch.
- Settings mới tách action/navigation/upload/analysis/result/lease/heartbeat/
  worker-stage timeout và đưa chúng vào startup diagnostics sanitized.
- Full-suite attempt đầu sau implementation đạt 383 pass/1 fail. Failure không
  phải CDHA: diagnostics contract cũ cho phép capture một page synthetic còn mở
  dù manager chưa attach. Fix tại ranh giới đúng: health vẫn `DISCONNECTED`, URL
  vẫn sanitized, nhưng target còn mở được capture; target `PAGE_CLOSED` không bị
  gọi title/screenshot/content. Focused regression **28 passed**.
- CDHA result URL không còn bị lẫn với upload URL: `#urlInput` được persist là
  `cdha_upload_url/UPLOADED`; external analysis ID chỉ parse từ URL `?view=` sau
  khi analysis đạt result boundary. Restart ở `SUBMITTING/UPLOADED/UNCERTAIN`
  không tự upload lại.
- Queue cuối pha 4 có atomic claim, owner, lease expiry, heartbeat, dynamic
  workflow `current_stage`, attempt/max-attempt và recovery `BLOCKED` khi crash
  vượt giới hạn. Worker race dispatch với lease-loss event, cancel stage khi
  mất ownership và dùng structured retryable `QUEUE_LEASE_EXPIRED`.
- Official read-only recovery inspection đã có `inspect-browser` và
  `inspect-queue`; queue output cố ý bỏ payload/clinical data.
- Sau khi tái tạo venv tạm, focused coverage cho CDHA/browser/queue/CLI/preflight
  đạt **105 passed**.
- Final full suite: **388 passed in 4.90s**, 0 failed, 0 skipped.
- Compile, tracked shell syntax, `git diff --check`, single active browser-launch
  ownership, no broad/force CDHA selector and no CDHA `wait_for_timeout` all pass.
- Real Quick preflight returned truthful **FAIL** solely because required
  `ffmpeg` is unavailable. Report mode is `0600` and secret pattern scan found
  no matches.
- Safe `config`, `inspect-queue`, and `inspect-browser` ran. Browser inspection
  reported `DISCONNECTED`, `cdp_ready=false`, no managed PID, and no lock; it did
  not launch Chrome. Full was not run without new authenticated authorization.
- Required 14-section evidence report:
  `docs/cdha-browser-reliability-report.md`.

---

# Findings — Prompt 3 Readiness Preflight

- Referenced-chat continuation verification on 2026-08-03 confirms the focused
  readiness suite passes **43/43** and the full repository suite passes
  **356/356** outside the restricted process sandbox.
- The sandbox-only full-suite hang occurs at
  `test_downloadreel_adapter.py::test_successful_adapter_normalization_and_sqlite_transitions`
  after repeated `Failed to create stream fd`; the identical suite completes in
  4.57 seconds outside the sandbox. This is environment isolation, not a
  production/test regression.
- Source compile, tracked shell syntax, static single-ownership audit and
  `git diff --check` all pass. Active source contains exactly one
  `PreflightReport` and one `run_preflight`.
- Current real Quick result remains truthfully **FAIL**: the only required
  failure is missing `ffmpeg`; cookie absence and inactive legacy paths are
  optional warnings, while every external probe is explicitly optional/skipped.
- Full preflight is now authorized and executed. It truthfully returns
  **FAIL**: `ffmpeg` missing; Ollama server unavailable (model/inference
  skipped); canonical browser lock free and manager startup passed; Facebook is
  `LOGIN_REQUIRED` (target skipped); CDHA authentication failed and selector
  probe skipped.
- Full and Quick JSON reports are mode `0600`. Secret-pattern scans of both
  reports and both browser diagnostic JSON files returned no matches. Browser
  artifacts contain only check name, sanitized URL/title and bounded state
  metadata.
- The live CDHA diagnostic exposes a classification gap: the page title is
  `@CDHa.ai • Đăng nhập`, but `_full_browser_checks` reports `UNKNOWN` because
  it only recognizes login markers in the URL before falling back from
  `is_authenticated=False` to `UNKNOWN`. Prompt 3 requires an explicit
  `LOGIN_REQUIRED` state when a login page is detectable, so this must receive a
  regression test and a source-level detector fix before final handoff.
- `CDHAWebClient.is_authenticated()` already checks the canonical
  `cdha.login_markers` registry, but returns only a boolean. The Full preflight
  discards the reason and independently checks URL/security markers, so a
  selector-visible login page at `/dash` becomes `UNKNOWN`. The smallest fix is
  to make the Full classifier explicitly reuse `cdha.login_markers` (and a
  sanitized title fallback for the live localized login title) before calling
  `is_authenticated`; no production authentication behavior needs changing.
- Inspection initially included nonexistent `app/cdha`; active CDHA code is
  `app/browser/cdha_client.py`. The incorrect path produced one harmless `rg`
  warning and will not be retried.
- Full browser probes are read-only in source: they acquire the canonical lock,
  use the official manager/profile, navigate temporary Facebook/CDHA pages,
  collect sanitized metadata on failure, close only those pages, and disconnect
  the manager. No downloader, upload, analysis, publish, comment, or job-state
  mutation appears in the Full probe call path.
- `promt.md` hiện có 1.072 dòng và là yêu cầu mới sau Prompt 1/2, không phải
  prompt browser-manager trong chat được tham chiếu.
- Definition of Done yêu cầu một official command `python -m app.main preflight`
  với Quick (local-only) và Full (read-only external probes).
- Điểm hồi quy bắt buộc phải tái hiện trước khi sửa là overall PASS trong khi
  Ollama bắt buộc nhưng `ollama_checked=false`.
- Full PASS đòi mọi required check đã chạy và pass; skipped, timeout, unknown,
  missing hoặc failed required check đều phải FAIL.
- Các probe Full không được download Reel, upload video, tạo phân tích, publish,
  comment, đổi trạng thái job, bypass auth hoặc gửi dữ liệu bệnh nhân.
- Worktree sạch trước khi planning files được cập nhật cho phiên 2026-08-03;
  commit hiện tại là `24d77e6`.
- Current preflight implementation is a single synchronous function in
  `app/preflight.py` with a flat `PreflightReport`; it has no per-check status,
  required flag, duration, category, aggregate verdict, mode, timeout state or
  report artifact.
- `run_preflight(..., require_ollama=False)` is the default. In that path Ollama
  is not contacted, `ollama_checked=False` is returned, and no exception/verdict
  prevents the caller from treating the run as successful.
- Current Ollama validation only requests `/api/tags`; it does not prove exact
  model availability, official-adapter inference, non-empty/parseable output or
  an explicit timeout result.
- The only visible official integration is currently `worker
  --preflight-only`; no unified `preflight --mode quick|full` subcommand exists.
- Existing `tests/test_preflight.py` primarily asserts fail-fast exceptions and
  does not cover structured verdict rules or Quick/Full side-effect boundaries.
- Root-cause hypothesis to verify at the CLI boundary: the caller interprets
  every non-exception flat report as success, while execution of required
  dependencies is controlled by an optional boolean rather than a mode/check
  matrix with completeness enforcement.
- CLI verification confirmed the hypothesis: `_run_official_command()` calls
  `run_preflight(settings, FacebookBrowserConfig.from_settings(settings))`
  without `require_ollama=True`, serializes the returned dataclass, and returns
  exit code 0 for `worker --preflight-only`. Thus `ollama_checked=false` is an
  expected successful result in the current official path.
- `DependencyContainer` eagerly creates browser config/directories, browser
  lock/manager, repositories, queue, pipeline, adapters/use cases and worker.
  Preflight must validate this graph without starting the browser or worker and
  without silently using a separate factory.
- The official analyzer factory is `app.ai.provider_factory.build_analyzer()`,
  producing `OllamaAnalyzer` over `OllamaClient`. The client already supports
  `/api/tags` model checks and `generate()`/`chat()`, so Full mode can reuse the
  official adapter boundary rather than introduce a second Ollama client.
- `Settings` already centralizes canonical browser profile/lock/cookie paths,
  adapter names and sanitized fingerprint, but it has no preflight-specific
  timeout fields or report directory yet.
- Current cookie inspection validates Netscape header/rows but exposes no
  size/format result object suitable for the new check matrix.
- Structured verdict contracts now enforce completeness independently of CLI:
  a required non-PASS result or an absent required check yields FAIL; optional
  warnings yield WARN only after all required checks pass.
- Full browser readiness can reuse one `FacebookBrowserManager`: `start()`
  acquires the canonical lock, starts/connects CDP and returns the shared
  context; `new_page()` creates owned temporary pages; `close()` disconnects
  Playwright and releases only its own lock without closing the shared browser.
- `FacebookStateDetector` already distinguishes login, two-factor, checkpoint,
  disabled/session/network/rate-limit and unknown states using URL, semantic
  selectors and bilingual text. UNKNOWN is not authenticated.
- `CDHAWebClient.is_authenticated()` is read-only but boolean; preflight must add
  explicit URL/security/selector outcomes around it.
- Real Quick result on 2026-08-03 is FAIL because `ffmpeg` is not installed.
  The canonical cookie is explicitly optional/missing (WARN), and four retained
  inactive legacy paths are WARN.
- Live Full execution is authorization-blocked: it would contact Ollama,
  Facebook and CDHA with the authenticated canonical browser profile. No live
  Full verdict exists, and the final report must use the external-readiness-
  blocked verdict.

---

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
