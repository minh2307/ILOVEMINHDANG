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
