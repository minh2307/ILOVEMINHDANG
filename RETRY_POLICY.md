# Retry Policy

## Cấu hình

`RetryPolicy` dùng số lần tối đa, initial delay, multiplier, maximum delay và jitter. Giá trị mặc định:

```text
RETRY_INITIAL_DELAY_SECONDS=0.5
RETRY_MULTIPLIER=2.0
RETRY_MAX_DELAY_SECONDS=8.0
RETRY_JITTER_SECONDS=0.25
```

Các giới hạn `MAX_DOWNLOAD_RETRIES`, `MAX_GEMINI_RETRIES`, `MAX_CDHA_RETRIES`, `MAX_FACEBOOK_PREPARE_RETRIES`, `MAX_PERMALINK_RETRIES`, `MAX_COMMENT_RETRIES` vẫn tương thích.

## Thuật toán

`retry_async()` gọi operation tối đa `max_attempts`, exponential backoff có cap và jitter. `sleep`/random/on-retry có thể inject để test deterministic. Permanent exception được raise nguyên bản; khi cạn attempts, raise `RetryExhaustedError` với cause và attempt metadata.

## Operation được retry

- Resolve/wait selector và CDHA iframe.
- Read-only navigation/polling.
- Permalink discovery và publication verification chỉ đọc.
- Diagnostic capture khi caller quyết định an toàn.

## Operation không retry trực tiếp

- Gemini submit.
- Upload video hoặc ảnh Facebook.
- Click CDHA Complete.
- Click Facebook Publish.
- Submit comment.

Sau timeout, caller phải reconciliation trước. Upload/publish không rõ kết quả chuyển sang uncertainty/manual action; tuyệt đối không click lại. Retry chỉ tiếp tục failed step, không reset job và không xóa artifact cũ.
