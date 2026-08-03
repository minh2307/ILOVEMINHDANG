# CDHA clinical-summary repair and real E2E report

Date: 2026-07-29  
Reel: `https://www.facebook.com/reel/1569069054789810`  
Workflow job: `1aab9d248a1b46338d592754d53011d8`

## Outcome

The label-leakage defect is fixed and verified against the real CDHA result.
The live workflow completed Reel download, caption persistence, frame extraction,
Ollama analysis, CDHA upload/analysis, structured result extraction, screenshot
capture, strict validation, and validated automatic medical review.

Facebook publication and permalink extraction were not executed because the
loaded environment is not configured for E2E publication:

- `TEST_MODE=false`
- no `FACEBOOK_TEST_TARGET_URL`
- production override is disabled
- the only configured production target is `/me`

The job is persisted at `APPROVED`. Its next queue item is intentionally left
unprocessed; no Facebook publish button was clicked and no permalink was
invented.

## Root causes

1. CDHA selector fallbacks used broad `get_by_text("Key findings:")` and
   `get_by_text("Impression:")` locators.
2. The extractor accepted the first non-empty locator text. A heading was
   therefore treated as a field value.
3. Line splitting converted `Key findings:` into a valid one-element findings
   list; `Impression:` remained a truthy impression.
4. The DTO discarded raw per-field values and exact analysis-URL provenance.
5. Review and post formatting rejected only empty values, not label-only data.
6. The final Facebook action did not revalidate the persisted caption against
   the structured CDHA result.
7. The official downloader still called an isolated legacy metadata/comments
   child queue. Media downloaded successfully, but the workflow timed out after
   180 seconds because no active worker served that old queue.
8. Test-mode target configuration was checked by the pipeline but ignored by
   the Facebook adapter at its side-effect boundary.

## Implemented repairs

- Added the authoritative `CDHAClinicalSummary` model with normalized findings,
  impression, exact analysis URL, source language, and raw field provenance.
- Added contextual DOM extraction for heading locators, including nested and
  following-sibling text, plus stable `data-key` and `data-field` selectors.
- Label prefixes and bullets are normalized while source measurements remain
  unchanged in the structured result.
- Invalid/label-only CDHA data now fails before `CDHA_ANALYZED`.
- Review approval validates required fields, Vietnamese content, PII, absolute
  claims, and exact analysis URL. Process-local auto-review uses the same rules;
  production remains manual by default.
- Post generation requires the exact Facebook source URL and exact CDHA result
  URL, configured hashtags, the required Vietnamese sections, and the complete
  professional disclaimer.
- Safe decimal display normalization is limited to measurements; raw CDHA data
  remains unchanged.
- The final Facebook click boundary verifies that every rendered finding and
  the impression match persisted CDHA values. Label-only or stale edited text
  cannot enter `FACEBOOK_PUBLISHING`.
- Test mode now uses `FACEBOOK_TEST_TARGET_URL`, rejects accidental equality
  with the production target unless explicitly overridden, and can suppress
  permalink comments.
- The official downloader no longer waits on the isolated child queue. It
  persists the verified yt-dlp caption and explicitly records an empty comments
  list with `metadata_extraction_status=yt_dlp_caption_only`; it does not claim
  that unavailable comments were extracted.

## Real workflow evidence

| Stage | Evidence | Result |
| --- | --- | --- |
| Job/queue | Official `create-job`; atomic worker claim and browser lock | Pass |
| Download | 14,421,435-byte video, SHA-256 persisted | Pass |
| Caption | Real Reel description persisted from yt-dlp | Pass |
| Comments | No verified comments available; explicit empty list/status | Partial, truthful |
| Frames | 12 frames extracted | Pass |
| Ollama | `VISION_FRAMES`, low confidence, one structural warning | Pass with warning |
| CDHA | Upload and analysis completed | Pass |
| Result URL | `https://cdha.ai/dash?view=44081` | Pass |
| Findings | Four real Vietnamese values; raw English heading retained separately | Pass |
| Impression | Real Vietnamese paragraph; raw English heading retained separately | Pass |
| Screenshots | `01-detailed-analysis.png`, `02-final-result.png` | Pass |
| Review | Strict validator passed; process-local auto-approval | Pass |
| Facebook preparation/publication | No separate E2E target configured | Blocked safely |
| Permalink/result | No publication occurred | Not available |

The first live download attempt exposed the legacy queue timeout and ended at
`DOWNLOADREEL_FAILED`. After the targeted fix, the same persisted job was moved
through `RETRY_PENDING` and resumed successfully without losing its history.

## Validated real post preview

```text
📌 CA LÂM SÀNG SIÊU ÂM

Video được phân tích bằng công cụ hỗ trợ chẩn đoán hình ảnh CDHA.AI.

🔍 Ghi nhận chính:
• Cấu trúc nang dịch vùng khoeo, ban đầu có dạng trống âm, ranh giới rõ, phù hợp với nang Baker.
• Kim tiêm được đưa vào bên trong nang dưới hướng dẫn của siêu âm liên tục.
• Qua chuỗi các khung hình, quan sát thấy nang xẹp dần, giảm thể tích rõ rệt, dịch bên trong có sự thay đổi âm vang do quá trình hút dịch hoặc bơm thuốc.
• Không quan sát thấy tín hiệu dòng chảy tĩnh hay động bất thường nào khác, thông số động học không đo được do thiếu chế độ Doppler.

📝 Nhận định:
Can thiệp nang Baker vùng khoeo trái dưới hướng dẫn siêu âm diễn ra thuận lợi. Dấu hiệu xẹp nang cho thấy thao tác hút dịch hoặc tiêm thuốc đã trúng đích. Cần kết hợp lâm sàng để theo dõi và xử lý nguyên nhân gốc.

⚠️ Nội dung được sử dụng cho mục đích tham khảo, chia sẻ và trao đổi chuyên môn.
Kết quả không thay thế việc thăm khám hoặc chẩn đoán trực tiếp của bác sĩ có chuyên môn.

Nguồn video:
https://www.facebook.com/reel/1569069054789810

Nguồn phân tích:
https://cdha.ai/dash?view=44081

#CDHA #SieuAm #ChanDoanHinhAnh #MedicalAI #HoiChan
```

The preview was produced by `PostContentService.build_post()` from the persisted
real structured result and passed `validate_publish_ready()`. It was not copied
from the formatting example and contains no example measurements.

## Automated verification

- Structured summary/model tests: 7 passed.
- Focused settings/extraction/review/Facebook tests: 83 passed.
- Downloader reliability tests: 18 passed.
- Final full repository suite: **295 passed in 6.86 seconds**.
- Python compilation: pass.
- `git diff --check`: pass.

One unit test was made independent of the live CDP process left by the real E2E
run. Production browser-owner validation was not weakened.

## Files changed for this repair

- `app/domain/models/cdha_clinical_summary.py`
- `app/domain/models/__init__.py`
- `app/models/results.py`
- `app/browser/cdha_client.py`
- `app/config/selectors.yaml`
- `app/services/post_content_service.py`
- `app/services/review_service.py`
- `app/browser/facebook_client.py`
- `app/config/settings.py`
- `app/adapters/facebook_adapter.py`
- `app/adapters/downloadreel_adapter.py`
- `app/infrastructure/legacy/dowloadReelFB/fb_downloader.py`
- `.env.example`
- regression tests under `tests/`

## Safe continuation

Before running another worker, configure a dedicated non-production destination:

```dotenv
TEST_MODE=true
FACEBOOK_TEST_TARGET_URL=https://www.facebook.com/<dedicated-e2e-target>
ALLOW_PRODUCTION_TARGET_IN_TEST_MODE=false
```

Then run `worker --once` to prepare the validated post, use the existing explicit
publish-confirmation command, and run the worker again for verified publication,
permalink persistence, and optional comment behavior. Do not use `/me` as a test
target unless that production-side effect is explicitly intended.
