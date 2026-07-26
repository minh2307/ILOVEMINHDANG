# Error Handling

## Taxonomy

`PipelineError` trong `app/errors.py` mang `error_code`, `retryable`, `manual_action_required`, `phase`, `operation`, `job_id`, `diagnostic_paths` và `details`. `str(error)` vẫn trả message thông thường để tương thích code cũ.

Nhóm chính:

- Browser: timeout/network/target closed/frame not ready/selector/auth/checkpoint.
- CDHA: upload, render và outcome upload không chắc chắn.
- Gemini: analysis và prompt safety.
- Facebook: publication, uncertain publication, verification.
- Privacy, invalid transition và repository.

Auth, checkpoint, prompt/privacy safety và publication uncertain không retry tự động; cần thao tác hoặc đối soát thủ công. Unknown browser error mặc định permanent.

## Playwright mapper

`app/browser/error_mapper.py` chỉ dùng public `playwright.async_api.Error` và `TimeoutError`. Mapper kết hợp type công khai, stable message marker và operation context. Target/page/context closed, frame detached, network/navigation, selector syntax, auth và checkpoint có mapping riêng. Target/frame/network/syntax được fail-fast trong selector fallback; timeout selector có thể thử candidate tiếp theo.

## Structured events

`JobRepository.record_error()` dùng `build_error_event_details()`. Event chỉ lưu metadata đã redaction: error code/type, retry/manual flags, phase/operation, attempt, safe URL không query/fragment, selector key và artifact path. Không lưu cookie, token, raw caption/comment, prompt, response hay DOM.

## Quy tắc caller

1. Map lỗi sát boundary Playwright.
2. Ghi event typed trước khi chuyển trạng thái failure/resumable.
3. Không chuyển lỗi uncertainty thành retry side effect.
4. Không nuốt target/frame/network error trong fallback.
5. Diagnostic capture là best-effort; lỗi capture không che lỗi gốc.
