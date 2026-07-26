# AI Input Security

## Trust model

Trusted instruction do ứng dụng kiểm soát. Caption và public comment từ Facebook là untrusted user-generated data, kể cả khi trông giống system/developer instruction.

`UntrustedContentService` thực hiện Unicode NFKC, bỏ null/control không cần thiết, chuẩn hóa whitespace, truncate theo limit và giữ ký hiệu/đơn vị y khoa. Detection chỉ gắn nhãn, không xóa toàn bộ comment.

## Injection signals

Các nhóm gồm ignore instructions, reveal system prompt, developer message, role/output override, safety bypass, do-not-analyze và follow-external-instructions. Một signal thường là MEDIUM; signal nghiêm trọng hoặc nhiều signal là HIGH. LOW tiếp tục bình thường; MEDIUM/HIGH tiếp tục có warning event và bắt buộc đi qua human review.

## Gemini boundary

Prompt gồm trusted rules, cảnh báo security, rồi:

```text
<UNTRUSTED_FACEBOOK_CONTENT>
Facebook caption: ...
Visible public comments: ...
</UNTRUSTED_FACEBOOK_CONTENT>
```

PII được mask trước normalization. Prompt không được log. `SAVE_RAW_GEMINI_PROMPT=false` và `SAVE_RAW_GEMINI_RESPONSE=false` là mặc định. Khi opt-in, artifact có quyền `0600` nhưng vẫn phải được coi là dữ liệu nhạy cảm.

Đầu ra phải đủ tám heading, plain text, không table/introduction/conclusion/definite diagnosis không có nguồn. Output chứa instruction-like markers hoặc delimiter bị reject. Không retry Gemini submit khi outcome không rõ và không bao giờ bỏ human review.

## Giới hạn

Pattern detection là defense-in-depth, không chứng minh nội dung an toàn. Không có external LLM safety call hoặc semantic classifier. Operator phải xem cả input risk, Clinical Factors và CDHA output.
