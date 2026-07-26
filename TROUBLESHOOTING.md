# Troubleshooting

## Profile Chrome đang được dùng

Thông báo profile lock/ProcessSingleton: đóng Chrome đang dùng đúng `CHROME_PROFILE_DIR`; không xóa lock khi process còn sống. Chạy lại `--show-job` rồi resume.

## Login hoặc security page

Dùng `--login-setup`/`--facebook-login-setup`, hoàn thành login, CAPTCHA, 2FA/checkpoint thủ công. Authentication/checkpoint là non-retryable cho đến khi operator xử lý.

## Selector/iframe lỗi

Xem `error_code`, attempted selector và diagnostics PNG/JSON. Target closed/network có thể retry theo typed metadata; selector syntax là permanent; frame detached phải resolve frame mới. Không thêm `body` làm result fallback vì gây false positive.

## CDHA upload timeout/uncertain

Kiểm tra view URL, result container, filename và upload status. Nếu bất kỳ dấu hiệu nào cho thấy upload/Complete có thể đã nhận, không upload/click lại. Resume sau reconciliation hoặc lưu bằng chứng và yêu cầu hỗ trợ.

## Gemini failure

Kiểm tra `gemini_input_risk_level`, suspicious pattern, validation warning và masked Clinical Factors. Không log prompt. Raw artifacts chỉ tồn tại khi flag opt-in. Không submit lại nếu chưa biết request trước đã được nhận chưa.

## Facebook publication uncertain

Không retry Publish. Đối chiếu post theo target, thời gian, text, số ảnh và permalink; nếu đã tồn tại, cập nhật/reconcile theo workflow thay vì tạo bản mới. Duplicate guard và fingerprint không được bypass trừ force mode có chủ đích.

## Privacy warning

Category/match count không chứa raw PII. Sửa hoặc mask nội dung văn bản rồi review lại. Text scan không nhìn được PII trong ảnh/video; mở screenshot folder/preview và kiểm tra thủ công.

## Diagnostics thiếu HTML

Đây là mặc định an toàn. PNG + JSON được tạo. Chỉ bật `SAVE_DIAGNOSTIC_HTML=true` tạm thời nếu thật sự cần, hạn chế quyền truy cập và tắt sau điều tra.

## Database

Không xóa/reset SQLite. Trước mọi restore, dừng writer và backup database hiện tại. Dùng `--list-resumable-jobs`, `--show-job` và event history để xác định step tiếp theo.

## Sandbox của môi trường phát triển

Nếu công cụ patch báo `bwrap: loopback: Failed RTM_NEWADDR`, đây là lỗi sandbox host, không phải pipeline. Trong đợt này unified diff trong `/tmp` và `git apply --no-index` được dùng làm fallback có kiểm soát.
