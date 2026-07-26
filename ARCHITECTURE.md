# Kiến trúc CDHA Automation Pipeline

## Phạm vi

Ứng dụng là workflow Python chạy cục bộ, tuần tự, lưu trạng thái trong SQLite và điều khiển Chrome bằng Playwright. Hệ thống không có web dashboard, multi-worker hoặc cơ chế tự vượt CAPTCHA/2FA/checkpoint.

## Luồng chính

```text
Facebook Reel
  -> DownloadReel
  -> Gemini Clinical Factors
  -> CDHA upload/analyze
  -> WAITING_FOR_REVIEW [human gate]
  -> APPROVED
  -> Facebook composer
  -> FACEBOOK_WAITING_FOR_MANUAL_REVIEW [human gate]
  -> publish + verification
  -> permalink/comment (nếu bật)
  -> COMPLETED
```

`WAITING_FOR_REVIEW` và `FACEBOOK_WAITING_FOR_MANUAL_REVIEW` là hard gate. Timeout, `--yes`, resume hay retry không được tự phê duyệt hoặc tự bấm Publish.

## Thành phần

- `app/main.py`: CLI và composition root.
- `app/workflows/`: state machine và orchestration có thể resume.
- `app/repositories/job_repository.py`: job/event append-only trên SQLite.
- `app/browser/`: Chrome lifecycle, selector resolution và client Gemini/CDHA/Facebook.
- `app/services/`: normalization, retry, privacy, untrusted content, review, screenshot và post content.
- `app/errors.py`: exception taxonomy duy nhất; `app/models/results.py` chỉ re-export alias cũ.
- `app/config/selectors.yaml`: selector UI và fallback theo dịch vụ.

## Dữ liệu và idempotency

Job data và event metadata là JSON mở rộng trong schema hiện hữu; không có migration trong đợt này. Job cũ vẫn đọc/resume được. `content_fingerprint`, hash ảnh, target URL và publication evidence bảo vệ chống đăng trùng. `--force-facebook-publish` chỉ là override rõ ràng và vẫn giữ hai xác nhận thủ công.

Các side effect Gemini submit, upload video, Complete, upload ảnh Facebook, Publish và comment không được retry mù. Khi outcome không xác định, pipeline fail-closed và yêu cầu operator reconciliation.

## Boundary bảo mật

Caption/comment là dữ liệu không tin cậy: normalize, mask PII, gắn risk, đặt trong delimiter rồi mới gửi Gemini. Đầu ra Gemini được validate và mask trước CDHA. Logging chạy credential + PII redaction. HTML diagnostics và raw Gemini artifacts mặc định tắt.

## Compatibility

- CLI, SQLite schema, status enum, screenshot names/order giữ nguyên.
- `SCREENSHOT_SECTIONS` là alias cùng object với `ScreenshotService.SECTIONS`.
- Exception/legacy result imports được re-export để tránh hierarchy song song.
- Không chạy live Facebook trong test mặc định.
