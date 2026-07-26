# Privacy

## Pipeline phát hiện

`PrivacyService.mask()` được giữ tương thích. Bên dưới, `RegexPIIDetector` và `CompositePIIDetector` triển khai `PIIDetector`; `PIIMatch` chỉ có category/span/confidence/detector và không chứa raw value.

`PrivacyScanResult` báo `safe_to_continue`, LOW/MEDIUM/HIGH, category, match count, yêu cầu manual review và warning. Event/log chỉ được ghi metadata này.

## Phạm vi regex

Phát hiện có kiểm soát: email chuẩn/ngụy trang, số điện thoại Việt Nam với separator, CCCD/CMND, MRN/mã bệnh nhân, tên/địa chỉ có nhãn, handle và URL. Không dùng free-form name regex rộng để tránh che thuật ngữ y khoa. Đơn vị, tuổi thai, huyết áp, kích thước và nhiệt độ phải được bảo toàn.

## Chính sách artifact và log

- Credential và PII được redaction trong message, nested extra và traceback.
- Raw Gemini prompt/response và browser HTML mặc định không lưu.
- Diagnostics tối thiểu gồm screenshot + metadata, quyền `0600`.
- Không đưa database, job artifacts, logs hoặc diagnostics lên kho công khai.
- Browser profile/cookie không nằm trong backup source được tạo cho đợt triển khai.

## Cảnh báo bắt buộc

Text privacy scan không phát hiện PII nằm trong video, frame hoặc screenshot. Operator phải kiểm tra thủ công tất cả hình ảnh trước approve/publish. OCR không được bật mặc định.

Regex không thể bảo đảm phát hiện mọi PII. HIGH không tự reject nội dung hợp lệ nhưng phải được operator xem; approval ghi nhận warning media, không biến thành auto-approval.
