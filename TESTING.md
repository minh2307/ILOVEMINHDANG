# Testing

## Lệnh chuẩn

```bash
.venv/bin/python -m compileall -q app
.venv/bin/python -m pytest --collect-only
.venv/bin/python -m pytest
```

Không bật live Facebook target, không dùng profile production và không gọi publish/comment trong test mặc định. Fake page/client phải khớp production interface.

## Coverage đợt triển khai

- Baseline contract: URL cụ thể, 2 screenshot canonical, compatibility alias/fake.
- Exception/error mapper: timeout, closed target, detached frame, network, syntax, unknown và redaction event.
- CDHA: iframe/dialog/input chậm, acknowledgement, resume/reconcile, uncertain upload và no body false-positive.
- Retry: first success, recovery, exhaustion, permanent/manual, backoff/jitter và không lặp side effect.
- AI security/privacy: delimiter, injection signals/output rejection, standard/obfuscated PII, measurement false-positive, metadata-only report.
- Diagnostics/logging: mặc định không HTML, safe URL, quyền file, nested extra và traceback redaction.
- Review/Facebook/state machine: hai hard gate, duplicate guard, force confirmation, image naming/order.

## Baseline và final

Baseline đã xác nhận: compile PASS; collection dừng vì một import error, tổng tiềm năng 198; khi cô lập có 178 pass và 3 failure. Sau Change Set 0: 198/198. Final: 224 collected, 224 passed, 0 failed/skipped/import error.

Các test browser dùng fake/controlled HTML; không chứng minh selector còn đúng với UI dịch vụ tại thời điểm chạy production. Login/challenge/live publication là external manual verification, không nằm trong suite tự động.
