# Kế hoạch: Xác minh Facebook publishing và chống đăng trùng (Prompt 5)

## Mục tiêu

Triển khai `promt.md` hiện tại: publication chỉ thành công khi có post ID hoặc
permalink chính xác đã được xác minh; validation bắt buộc trước side effect;
attempt/fingerprint bền vững chống duplicate và crash; uncertain publication
phải reconciliation thủ công/an toàn; không publish Facebook thật trong test.

## Nguyên tắc an toàn

- Bảo toàn toàn bộ thay đổi Prompt 1–4 chưa commit và dữ liệu runtime.
- Không xóa/chỉnh database, profile, cookie, log, lock sống hoặc artifact gốc.
- Không thực hiện live publish khi chưa có Full readiness và quyền rõ ràng.
- Không xem click/dialog đóng/navigation/timeout là bằng chứng published.
- Không auto-retry attempt ở `SUBMITTING`/`SUBMITTED_UNCONFIRMED`.
- Root cause phải dựa trên persisted evidence và call chain; có test đỏ trước fix.

## Các giai đoạn

- [x] **Pha 1 — Evidence và call graph:** đọc trọn Prompt 5, inventory publisher,
  persisted errors/IDs/permalinks/diagnostics, official confirm-publish path và
  baseline 388 tests.
- [x] **Pha 2 — Contracts/state/validation:** result model, authoritative state
  integration, mandatory validation gate và deterministic fingerprint.
- [x] **Pha 3 — Durable attempt và publisher:** attempt repository/migration,
  pre-click `SUBMITTING`, composer/media/caption verification và structured auth.
- [x] **Pha 4 — Post verification/reconciliation:** exact ID/permalink evidence,
  bounded reconciliation, duplicate prevention, restart/resume behavior và CLI.
- [x] **Pha 5 — Diagnostics/tests/docs:** sanitized artifacts, controlled failure
  fixtures, operations docs và evidence report.
- [x] **Pha 6 — Verification:** focused/full suite, compile/static/diff, Quick/
  Full report thực tế và controlled-publication verdict trung thực.

## Trạng thái hiện tại

- Pha 1–6: `complete`
- Baseline kế thừa: **388 passed**, full suite passed 411 tests.

## Lỗi gặp phải

| Lỗi | Lần thử | Cách xử lý |
|---|---:|---|
| Lệnh đọc ghép phát cảnh báo `Failed to create stream fd` nhưng vẫn trả đủ prompt metadata/diff | 1 | Không suy ra test failure; tiếp tục đọc theo vùng nhỏ và chỉ escalate nếu lệnh kiểm thử thực sự không hoàn tất |
| Host không có alias `python` khi mở SQLite read-only | 1 | Dùng executable `python3` hiện có; không lặp lại lệnh `python` và không sửa environment |
| Baseline full suite trong sandbox dừng ở ~27% sau `Failed to create stream fd`, không exit/summary | 1 | Dừng tiến trình treo; chạy lại cùng lệnh ngoài sandbox hạn chế như các phiên đã xác nhận |

---

# Kế hoạch: Sửa độ tin cậy CDHA và browser automation (Prompt 4)

## Mục tiêu

Triển khai `promt.md` hiện tại: dùng bằng chứng lỗi persisted để làm workflow
CDHA/browser dùng chung có ownership rõ ràng, state-aware, lease/heartbeat/retry
an toàn, timeout hữu hạn, selector bền vững và diagnostics được làm sạch; không
thực hiện publish Facebook thật và không báo readiness/success khi chưa kiểm tra.

## Nguyên tắc an toàn

- Bảo toàn toàn bộ thay đổi Prompt 3 đang chưa commit và dữ liệu runtime.
- Không xóa/chỉnh database, profile, cookie, lock sống hay artifacts gốc.
- Không mở browser thật hoặc gọi CDHA/Facebook nếu chưa cần và chưa được phép.
- Root cause phải có bằng chứng source/persisted fixture; test hồi quy đỏ trước
  khi sửa hành vi.
- Không tự động retry external action nếu đã có CDHA external ID/result URL.

## Các giai đoạn

- [x] **Pha 1 — Failure evidence và baseline:** đọc trọn prompt, inventory DB/
  queue/log/artifact/lock/source và chạy baseline an toàn.
- [x] **Pha 2 — Lifecycle/health contracts:** xác định owner duy nhất, health
  states, page acquire/release/reconnect và structured browser errors.
- [x] **Pha 3 — CDHA state machine/selectors/waits:** registry selector, semantic
  completion states, bounded waits, recovery theo persisted external identity.
- [x] **Pha 4 — Queue reliability:** lease, heartbeat, stale recovery, timeout
  policy, retry taxonomy/idempotency và crash-safe transitions.
- [x] **Pha 5 — Diagnostics và regression tests:** artifacts sanitized/capped,
  fixtures cho các failure mode và integration tests không external side effect.
- [x] **Pha 6 — Verification/report:** full suite, compile/static/diff, safe
  non-destructive checks và báo cáo bằng chứng.

## Trạng thái hiện tại

- Pha 1: `complete`
- Pha 2: `complete`
- Pha 3: `complete`
- Pha 4: `complete`
- Pha 5–6: `complete`
- Automated verification: **388 passed, 0 failed, 0 skipped**; compile,
  tracked shell syntax, static ownership/selector audit and diff check pass.
- Real Quick: `FAIL` because host `ffmpeg` is missing.
- Full: `NOT EXECUTED — AUTHORIZATION BLOCKED`.

## Lỗi gặp phải

| Lỗi | Lần thử | Cách xử lý |
|---|---:|---|
| Output khảo sát đầu bị truncate vì planning files chứa nhiều phiên | 1 | Đọc `promt.md` và source theo từng vùng nhỏ; ghi findings sau mỗi hai lượt xem |
| Host không có executable `sqlite3` khi query canonical DB read-only | 1 | Dùng module `sqlite3` chuẩn của Python cho các SELECT read-only, không cài package hay sửa DB |
| Temporary venv `/tmp/minhdang-preflight-venv` đã bị dọn trước baseline | 1 | Kiểm tra interpreter/dependency hiện có; nếu cần tạo lại venv dưới `/tmp`, không sửa `.venv` dự án |
| Repository `.venv` dùng Python 3.14 nhưng không load được `pytest` | 1 | Tạo `/tmp/minhdang-prompt4-venv` với Python 3.13.5; giữ `.venv` nguyên trạng |
| Cài requirements vào venv tạm thất bại do sandbox DNS | 1 | Retry qua quyền network được phê duyệt; 18 packages cài thành công dưới `/tmp` |
| Full suite trong sandbox dừng khoảng 30% không có exit/summary | 1 | Chạy lại cùng lệnh ngoài sandbox hạn chế; baseline **357 passed in 5.51s** |
| Audit tham chiếu nhầm `app/domain/services/job_state_transitions.py` | 1 | Dùng `rg` định vị transition implementation thật trước khi đọc; không lặp lại path sai |
| Audit tham chiếu nhầm `app/domain/models/workflow_job.py` và `entities/job.py` | 1 | Model thật là `app/domain/models/job.py`; dùng kết quả `rg` thay vì đoán path |
| Full suite đầu sau Prompt 4: 1 fail diagnostics unpack (383 pass) | 1 | Tách manager health khỏi khả năng capture của page synthetic còn mở; closed page vẫn không bị chạm. Focused rerun 28 pass |
| Patch docs nhiều file lệch context tại README nên không áp dụng | 1 | Tách patch theo từng file và dùng heading/đoạn thực tế sau khi đọc context; không lặp patch gộp |
| Temporary Prompt 4 venv bị dọn sau lượt tiếp tục của người dùng | 1 | Tạo lại dưới `/tmp` từ requirements; không sửa `.venv` dự án |

---

# Kế hoạch: Nâng preflight thành kiểm tra readiness end-to-end trung thực

## Mục tiêu

Triển khai `promt.md` hiện tại (Prompt 3): thay preflight nông bằng một hệ thống
Quick/Full duy nhất dưới unified CLI, có check/result/verdict cấu trúc, timeout,
diagnostics được làm sạch và kiểm thử hồi quy; không tạo external side effect.

## Nguyên tắc an toàn

- Không xóa profile, cookie, lock sống, database hay dữ liệu runtime.
- Quick chỉ kiểm tra local; Full chỉ thực hiện probe đọc-only.
- Không upload, phân tích ca bệnh, publish, comment hay đổi trạng thái job.
- Required check bị skip/timeout/unknown/missing luôn làm verdict FAIL.
- Không in/lưu cookie, token, mật khẩu, HTML nhạy cảm hoặc dữ liệu bệnh nhân.

## Các giai đoạn

- [x] **Pha 1 — Current-state audit và reproduction:** lần official CLI/call
  graph, tái hiện `ollama_checked=false` nhưng PASS, lập inventory và baseline.
- [x] **Pha 2 — Structured contracts và verdict:** model check/report, matrix
  Quick/Full, timeout và regression tests đỏ trước khi sửa.
- [x] **Pha 3 — Quick readiness:** runtime, settings, paths, tools, DB/schema,
  cookie/config, composition-root và adapter wiring không external action.
- [x] **Pha 4 — Full readiness:** Ollama health/model/inference, browser/lock,
  Facebook auth/target, CDHA auth/selectors và read-only diagnostics.
- [x] **Pha 5 — CLI/reporting:** `app.main preflight`, concise/verbose output,
  exit code và JSON report sanitized.
- [x] **Pha 6 — Verification/report:** focused tests, full suite, quick/full
  preflight, compile/static/diff checks và evidence report.

## Trạng thái hiện tại

- Pha 1–6: `complete`
- Automated verification: **357 passed, 0 failed, 0 skipped**; focused 44
  passed; compile, shell syntax, static ownership audit and diff check pass.
- Real Quick: `FAIL` (host missing `ffmpeg`), exit code 1.
- Real Full: `FAIL`, exit code 1. Browser startup passed; blockers are missing
  `ffmpeg`, Ollama server unavailable, Facebook `LOGIN_REQUIRED`, and CDHA
  `LOGIN_REQUIRED`. Dependent required checks remain skipped, so external
  readiness is explicitly not claimed.

---

# Kế hoạch: Hợp nhất Chrome profile, session, cookie và cấu hình

## Mục tiêu

Triển khai `promt.md` ngày 2026-07-30: dùng một typed settings source cho toàn
bộ official CLI/Worker/browser CLI/scripts/downloader/publisher/CDHA/preflight,
chuẩn hóa profile lock và cookie path, thêm chẩn đoán được làm sạch, migration
report và regression tests mà không đụng dữ liệu phiên/cookie/runtime thật.

## Nguyên tắc an toàn

- Bảo toàn toàn bộ worktree chưa commit và dữ liệu runtime hiện có.
- Không mở browser thật, không đăng nhập/publish, không đọc hoặc in nội dung
  cookie/storage-state.
- Không tự di chuyển, hợp nhất hay xóa profile/cookie.
- Source và call graph thực tế quyết định thành phần active; file tồn tại không
  đồng nghĩa đang dùng.

## Các giai đoạn

- [x] **Pha 1 — Inventory và official dependency graph:** tìm mọi profile,
  cookie, settings factory, CLI/script/config/doc reference và xác định active.
- [x] **Pha 2 — Canonical typed configuration:** hợp nhất settings/factory/path
  resolution/conflict validation/fingerprint và migration warnings.
- [x] **Pha 3 — Active integration:** đưa Worker, browser CLI/manager/scripts,
  downloader, publisher, CDHA và preflight về cùng settings + lock.
- [x] **Pha 4 — Diagnostics, docs và migration:** config inspection, startup
  diagnostics, git secret safety, tài liệu và báo cáo đường dẫn legacy.
- [x] **Pha 5 — Verification:** regression tests theo prompt, full suite,
  compile, shell syntax, static audit và `git diff --check`.

## Trạng thái hiện tại

- Pha 1–5: `complete`
- Verification cuối: **345 passed, 0 failed, 0 skipped**; compile, shell syntax, preflight, config parity và `git diff --check` đều pass.

## Lỗi gặp phải

| Lỗi | Lần thử | Cách xử lý |
|---|---:|---|
| Sandbox `bwrap: loopback: Failed RTM_NEWADDR` khi chạy `wc -l promt.md` | 1 | Chạy lại thao tác đọc-only theo quyền đã phê duyệt; xác nhận prompt có 628 dòng |
| Full suite bị 72 lỗi cùng conflict profile | 1 | Truy ra module legacy gọi `load_dotenv()` lúc import; đổi sang lookup dotenv cục bộ không làm biến đổi môi trường tiến trình |

---


## Mục tiêu

Đối chiếu `promt.md` hiện tại với refactor đang có, rồi hoàn tất một CLI
subcommand duy nhất tại `app/main.py`, một cơ chế transition duy nhất, và các
luồng retry/resume/legacy delegation không còn bypass luật trạng thái.

## Nguyên tắc an toàn

- Bảo toàn toàn bộ worktree chưa commit hiện có; không reset hay ghi đè thay đổi
  không thuộc phần việc này.
- Không chạy publish Facebook thật và không sửa dữ liệu runtime.
- Mọi kết luận phải dựa trên call chain/source/test thực tế.

## Các giai đoạn

- [x] **Pha 1 — Inventory và gap analysis:** đọc toàn bộ prompt, CLI entrypoint,
  orchestration, transition và tài liệu; lập bảng đường gọi thực tế.
- [x] **Pha 2 — Regression tests:** thêm test tái hiện các bypass/duplicate còn
  tồn tại trước khi sửa.
- [x] **Pha 3 — Implementation:** hợp nhất composition root, transition,
  retry/resume và legacy wrappers theo gap đã xác nhận.
- [x] **Pha 4 — Documentation và migration:** cập nhật inventory/migration map,
  lệnh chính thức và cảnh báo deprecation.
- [x] **Pha 5 — Verification:** focused tests, full suite, compile, shell syntax,
  static audit và `git diff --check`.

## Trạng thái hiện tại

- Pha 1–5: `complete`
- Verification cuối: **332 passed**, compile/shell/diff/static audit pass.

---

# Kế hoạch: Viết hướng dẫn chạy dự án

## Mục tiêu

Tạo một file Markdown tiếng Việt hướng dẫn cài đặt, cấu hình, kiểm tra và chạy dự án từng bước, dựa trên các entrypoint và script thực tế.

## Các giai đoạn

- [x] Xác định phạm vi và quy trình khảo sát.
- [x] Khảo sát dependency, biến môi trường, script và entrypoint.
- [x] Xác định các chế độ chạy và thứ tự dịch vụ.
- [x] Viết tài liệu hướng dẫn.
- [x] Kiểm tra lại mọi lệnh và liên kết trong tài liệu.

## Lỗi gặp phải

- Một số lệnh shell ghép nhiều thao tác bị sandbox báo `bwrap: loopback: Failed RTM_NEWADDR`; chuyển sang các lệnh đọc đơn lẻ.
- `task_plan.md`, `findings.md`, `progress.md` từng xuất hiện ở lần liệt kê đầu nhưng không còn khi đọc lại; tạo mới để phục vụ phiên này.

---

# Kế hoạch: Hợp nhất và tái cấu trúc ứng dụng tự động hóa

## Mục tiêu

Khảo sát toàn bộ mã nguồn, lập baseline an toàn, rồi tái cấu trúc tăng dần thành một ứng dụng Python có một CLI chính thức, một workflow có trạng thái bền vững, các ranh giới domain/application/infrastructure/interfaces rõ ràng, khả năng retry/resume, và tài liệu kiểm chứng đầy đủ.

## Nguyên tắc an toàn

- Không xóa dữ liệu runtime, profile trình duyệt, cơ sở dữ liệu, hay công việc chưa commit.
- Mã nguồn thực tế là nguồn sự thật; tài liệu chỉ là ý định cần đối chiếu.
- Mỗi thay đổi phải có kiểm thử tương ứng trước khi chuyển pha.
- Không thực hiện publish thật trong kiểm thử.

## Các giai đoạn

- [x] **Pha 1 — Inventory và baseline:** cây repo, tài liệu, entrypoint, dependency, cấu hình, trạng thái Git, kiểm thử hiện tại, báo cáo trước refactor.
- [x] **Pha 2 — Domain và contracts:** mô hình duy nhất, enum trạng thái, luật chuyển trạng thái, DTO và ports.
- [x] **Pha 3 — Configuration và runtime paths:** settings có kiểu, xác thực, đường dẫn từ project root.
- [x] **Pha 4 — Persistence và queue:** SQLite repository/queue, claim nguyên tử, lease/heartbeat/recovery, lịch sử trạng thái.
- [x] **Pha 5 — Browser management:** Playwright session dùng chung, profile lock, selector/diagnostic/cleanup.
- [x] **Pha 6 — Adapter hóa tính năng đang chạy:** downloader, analyzer, CDHA, Facebook publishing.
- [x] **Pha 7 — Unified workflow:** use case điều phối duy nhất, idempotency, retry/resume.
- [x] **Pha 8 — Worker và orchestrator:** trách nhiệm tách biệt, heartbeat và stale recovery.
- [x] **Pha 9 — CLI và legacy migration:** một `main.py`, entrypoint cũ delegate/deprecate khi an toàn.
- [x] **Pha 10 — Cleanup, docs và verification:** tài liệu, migration map, toàn bộ test, checklist thủ công.

## Trạng thái hiện tại

- Pha 1: `complete`
- Pha 2–10: `complete`
- Verification cuối: compile pass, shell syntax pass, `git diff --check` pass, **280 tests passed**.

## Lỗi gặp phải trong phiên refactor

| Lỗi | Lần thử | Cách xử lý |
|---|---:|---|
| Sandbox báo `bwrap: loopback: Failed RTM_NEWADDR` khi đọc template và tìm planning files | 1–2 | Chuyển các thao tác đọc-only cần thiết sang execution đã được phê duyệt bên ngoài sandbox |

---

# Kế hoạch: Sửa dữ liệu tóm tắt CDHA và kiểm thử E2E Reel

## Mục tiêu

Truy vết điểm đầu tiên làm mất hoặc thay thế nội dung `key_findings` và
`impression`, sửa toàn bộ contract dữ liệu có cấu trúc, chặn publish khi dữ liệu
lâm sàng không hợp lệ, rồi kiểm chứng bằng test tự động và workflow thật ở chế
độ E2E đã cấu hình mà không thay đổi quy tắc review thủ công của production.

## Các giai đoạn

- [x] **Pha 1 — Tái hiện và truy vết:** khảo sát selector/extractor/parser/DTO/persistence/formatter/publish guard và tạo reproduction tối thiểu.
- [x] **Pha 2 — Contract và regression tests:** thêm model `CDHAClinicalSummary`, fixtures nested DOM/label leakage, format và validation tests đang fail.
- [x] **Pha 3 — Sửa root cause:** sửa extraction/normalization/persistence theo bằng chứng, không hard-code dữ liệu y khoa.
- [x] **Pha 4 — Defense in depth:** validation tại review và ngay trước publish; production vẫn manual, E2E auto-review chỉ khi hợp lệ.
- [x] **Pha 5 — Verification tự động:** focused tests, full suite, compile và diff checks.
- [ ] **Pha 6 — Real E2E:** chạy Reel được chỉ định qua official workflow, thu thập chẩn đoán và trạng thái/permalink bền vững; không báo thành công nếu môi trường hoặc external service chặn.
- [ ] **Pha 7 — Báo cáo:** root cause, file thay đổi, dữ liệu qua từng boundary, bằng chứng test/E2E và rủi ro còn lại.

## Trạng thái hiện tại

- Pha 1–2: `complete`
- Pha 1–4: `complete`
- Pha 1–5: `complete`
- Pha 6: `blocked` tại Facebook publication vì chưa cấu hình E2E target riêng; các bước trước publish đã hoàn tất.
- Pha 7: `complete`

## Ràng buộc

- Không dùng nội dung ví dụ làm dữ liệu thật và không bịa finding/measurement.
- Không publish nếu finding/impression rỗng, chỉ là nhãn, chứa PII, hoặc thiếu source/result URL.
- Không tự động review trong production trừ khi cấu hình production bật rõ ràng.
- Không in secret/cookie/profile data trong log hay báo cáo.
