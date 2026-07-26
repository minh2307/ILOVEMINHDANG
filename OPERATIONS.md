# Operations

## Khởi tạo và kiểm tra

```bash
.venv/bin/python main.py --check-config
.venv/bin/python main.py --init-db
.venv/bin/python main.py --list-resumable-jobs
.venv/bin/python main.py --show-job JOB_ID
```

Bắt đầu bằng `--reel-url URL`; dùng `--run-until-review JOB_ID`, sau đó `--review-job JOB_ID`. Job APPROVED tiếp tục bằng `--continue-approved-job JOB_ID`. Cổng Facebook cuối vẫn yêu cầu lựa chọn Publish rõ ràng.

## Resume và retry

- `--resume-job JOB_ID`: tiếp tục từ state hiện tại.
- `--retry-job JOB_ID`: retry failed step theo metadata, không reset từ đầu.
- `--cancel-job JOB_ID`: dừng an toàn, không xóa artifact/job.
- Không dùng `--force-download` hoặc `--force-facebook-publish` nếu chưa reconciliation. Force publish vẫn yêu cầu hai xác nhận và có nguy cơ duplicate.

## Login/CAPTCHA/2FA/checkpoint

Dùng `--login-setup` cho Gemini/CDHA và `--facebook-login-setup` cho Facebook. Hoàn thành login, CAPTCHA, 2FA hoặc checkpoint thủ công trong Chrome rồi xác nhận CLI. Không dùng automation để vượt challenge. Nếu profile đang mở, đóng đúng Chrome dùng profile pipeline rồi resume.

## Uncertainty

- `CDHA_UPLOAD_UNCERTAIN`: kiểm tra filename/status/view URL/result artifact trước khi upload lại.
- Facebook publication uncertain: kiểm tra Page/profile, thời gian, text, ảnh và permalink. Không bấm Publish lại cho đến khi kết luận bài chưa tồn tại.
- Auth/checkpoint/manual-action: xử lý thủ công rồi resume; không retry loop.

## Diagnostics và privacy

Mặc định diagnostics là PNG + JSON metadata. Chỉ bật `SAVE_DIAGNOSTIC_HTML=true`, `SAVE_RAW_GEMINI_PROMPT=true` hoặc `SAVE_RAW_GEMINI_RESPONSE=true` trong phiên điều tra có kiểm soát; tắt lại sau đó và bảo vệ artifact `0600`. Xem risk/categories trong job event, nhưng luôn kiểm tra PII trực quan trong video/screenshot.

## Backup, restore và rollback

Backup DB vận hành:

```bash
.venv/bin/python main.py --backup-db
```

Dừng pipeline/Chrome trước restore. Xác minh đường dẫn `DATABASE_PATH`, sao lưu bản hiện tại, rồi thay bằng bản SQLite đã kiểm tra quyền và chạy `--show-job`/test đọc. Không restore khi có process đang ghi.

Workspace root không có Git history. Backup trước triển khai:

```text
backups/source_pre_part_b_20260726T063528Z.tar.gz
data/jobs_backup_20260726T063550Z.sqlite3
```

Source archive không chứa `.env`, cookie hoặc browser profile. Rollback: safe shutdown, backup trạng thái hiện tại, giải nén source archive vào staging để kiểm tra rồi chép có kiểm soát; restore SQLite chỉ khi cần rollback dữ liệu và sau khi xác minh bản backup. Không tuyên bố rollback bằng commit.

## Safe shutdown

Không kill trong lúc upload/Publish/comment. Chờ operation kết thúc hoặc ghi nhận uncertainty, dùng Ctrl-C một lần, đóng Chrome đúng profile, kiểm tra `--show-job`, rồi backup DB. Resume từ persisted state.
