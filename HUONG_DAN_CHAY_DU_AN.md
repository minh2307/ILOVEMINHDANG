# Hướng dẫn chạy dự án CDHA Facebook Workflow

Tài liệu này hướng dẫn cài đặt và chạy dự án trên Linux theo từng bước. Tất cả lệnh bên dưới cần được chạy từ thư mục gốc của dự án.

Kiểm tra phục hồi an toàn, không mở Chrome và không sửa database:

```bash
python -m app.main inspect-browser
python -m app.main inspect-queue
python -m app.main status --job-id <job-id>
```

Không xóa lock đang sống hoặc sửa SQLite bằng tay. Nếu CDHA đã có external ID
hoặc result URL, `resume` tiếp tục từ kết quả đó. Nếu lần submit cũ chưa chắc
chắn, hệ thống chặn upload lại và yêu cầu đối soát.

## 1. Tổng quan luồng xử lý

Dự án nhận URL Facebook Reel và xử lý theo luồng:

1. Tải video Reel và metadata.
2. Trích xuất frame bằng `ffmpeg` nếu được bật.
3. Phân tích dữ liệu bằng Ollama.
4. Gửi video và Clinical Factors lên CDHA.
5. Dừng tại bước duyệt thủ công.
6. Sau khi được duyệt, chuẩn bị và đăng bài lên Facebook.

Dự án có hai cách chạy:

- **CLI pipeline:** phù hợp để chạy trực tiếp, kiểm tra từng bước và khôi phục job.
- **Orchestrator + worker:** dùng hàng đợi SQLite; phù hợp khi muốn worker chạy lâu dài.

## 2. Điều kiện cần

Cần chuẩn bị:

- Linux.
- Python 3.10 trở lên.
- Google Chrome.
- `ffmpeg` và `ffprobe` nếu bật trích xuất frame.
- Tài khoản có thể đăng nhập Facebook, Gemini và CDHA.
- Ollama cùng một model đã tải nếu chạy toàn bộ bước phân tích AI.
- Kết nối Internet đến Facebook, Gemini và CDHA.

Kiểm tra nhanh:

```bash
python3 --version
google-chrome --version
ffmpeg -version
ollama --version
```

Nếu lệnh `google-chrome` không tồn tại, hãy tìm đường dẫn Chrome thực tế và khai báo đường dẫn đó trong `.env` ở bước 5.

## 3. Đi vào thư mục dự án

```bash
cd /media/nguyen-son-minh/p5/MinhDang
```

Các đường dẫn cookie và một số script là đường dẫn tương đối, vì vậy không nên chạy lệnh từ thư mục khác.

## 4. Tạo môi trường Python và cài dependency

Nếu chưa có `.venv`:

```bash
python3 -m venv .venv
```

Cài dependency:

```bash
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Kiểm tra Playwright:

```bash
.venv/bin/python -c "import playwright; print('Playwright OK')"
```

Dự án kết nối đến Google Chrome qua CDP nên luồng chính không bắt buộc dùng Chromium do Playwright tải riêng.

## 5. Tạo và chỉnh sửa `.env`

Chỉ thực hiện lệnh sao chép nếu chưa có `.env`:

```bash
cp .env.example .env
```

Không đưa `.env`, cookie, token hoặc profile Chrome lên Git.

Mở `.env` và kiểm tra tối thiểu các biến sau:

```dotenv
CHROME_EXECUTABLE_FALLBACK=/usr/bin/google-chrome
FACEBOOK_CHROME_EXECUTABLE=/usr/bin/google-chrome
FACEBOOK_TARGET_URL=https://www.facebook.com/<page-hoac-profile-dich>

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=<ten-model-da-cai>

GEMINI_URL=https://gemini.google.com/app
CDHA_URL=https://cdha.ai/dash?modality=us_video&country=VN
```

Nếu Chrome nằm tại `/opt/google/chrome/google-chrome`, thay cả hai đường dẫn Chrome bằng đường dẫn đó.

Các thiết lập an toàn nên giữ khi chạy thử:

```dotenv
HEADLESS=false
FACEBOOK_BROWSER_HEADLESS=false
FACEBOOK_FINAL_CONFIRMATION=true
DOWNLOADREEL_ENABLE_INTERACTIONS=false
ENABLE_REEL_LIKE=false
ENABLE_COMMENT_LIKE=false
AUTO_APPROVE_REVIEW=false
```

Muốn chạy bằng Ollama:

```bash
ollama serve
```

Mở terminal khác, tải model nếu máy chưa có model đó:

```bash
ollama pull <ten-model>
```

Giá trị `<ten-model>` phải giống `OLLAMA_MODEL` trong `.env`. Nếu cần phân tích hình ảnh từ frame, sử dụng model Ollama có khả năng vision.

## 6. Chuẩn bị xác thực Facebook

Không ghi cookie vào log, tài liệu hoặc Git. Official Reel downloader chỉ đọc
một file Netscape do `FACEBOOK_COOKIE_FILE` cấu hình, mặc định:

```text
runtime/auth/facebook_cookies.txt
```

File này là tùy chọn khi `FACEBOOK_COOKIE_REQUIRED=false`; nếu thiếu, downloader
ghi nhận phương thức `anonymous` và không fallback sang file khác. Nếu file tồn
tại nhưng unreadable hoặc sai Netscape format, job dừng với chẩn đoán chỉ chứa
path/trạng thái, không chứa nội dung cookie.

Facebook publisher và CDHA không dùng file cookie này. Hai thành phần đó xác
thực bằng Chrome profile persistent duy nhất từ `CHROME_PROFILE_DIR`, mặc định:

```text
runtime/chrome_profiles/cdha_automation
```

Các path `Cookie.txt`, `runtime/cookies.txt`, cookie trong package legacy và
profile `runtime/chrome_profiles/facebook` chỉ là dữ liệu cũ, không active và
không được tự động chọn, di chuyển, hợp nhất hoặc xóa.

## 7. Kiểm tra cấu hình trước khi chạy

Kiểm tra CLI pipeline mà không mở Chrome:

```bash
.venv/bin/python main.py --check-config
```

Kết quả cần kết thúc bằng `Config check: PASS`. Sửa tất cả mục `FAIL` trước khi tiếp tục.

Kiểm tra local mà không nhận job, không mở Chrome, không gọi Ollama inference và
không truy cập Facebook/CDHA:

```bash
.venv/bin/python -m app.main preflight --mode quick
```

Kiểm tra Full chỉ đọc (dùng Chrome profile chính thức và truy cập
Ollama/Facebook/CDHA) khi đã được phép dùng session đăng nhập:

```bash
.venv/bin/python -m app.main preflight --mode full
.venv/bin/python -m app.main preflight --mode full --verbose
```

Mọi required check bị `failed`, `skipped`, `timeout`, `unknown` hoặc không chạy
đều làm kết quả `FAIL` và exit code 1. Quick PASS chỉ có nghĩa local readiness;
chỉ Full PASS mới có nghĩa external readiness đã được xác minh.

Khởi tạo cơ sở dữ liệu của pipeline:

```bash
.venv/bin/python main.py --init-db
```

## 8. Đăng nhập thủ công lần đầu

Dự án lưu session trong Chrome profile dưới `runtime/chrome_profiles/`. Không chạy đồng thời nhiều Chrome dùng cùng profile.

Đăng nhập Gemini và CDHA:

```bash
.venv/bin/python main.py --login-setup
```

Trong cửa sổ Chrome:

1. Đăng nhập Gemini.
2. Đăng nhập CDHA.
3. Hoàn tất 2FA, CAPTCHA hoặc checkpoint nếu có.
4. Quay lại terminal và nhấn Enter để chương trình xác minh.

Đăng nhập Facebook:

```bash
.venv/bin/python main.py --facebook-login-setup
```

Đăng nhập trong Chrome, hoàn tất xác minh rồi quay lại terminal nhấn Enter. Lệnh này không mở composer và không đăng bài.

## 9. Quy trình chính thức — subcommand + worker

Đây là đường chạy có thẩm quyền duy nhất. Không chạy pipeline trực tiếp bằng legacy flags.

### 9.1 Kiểm tra an toàn, không claim job

```bash
.venv/bin/python -m app.main preflight --mode quick
.venv/bin/python -m app.main preflight --mode full
```

### 9.2 Tạo job và xử lý đến checkpoint duyệt

```bash
.venv/bin/python main.py create-job --url "https://www.facebook.com/reel/REEL_ID"
.venv/bin/python main.py orchestrator --once
.venv/bin/python main.py worker --once
```

Ghi lại `job_id` được in ra. Pipeline sẽ dừng tại `WAITING_FOR_REVIEW` và hiển thị lệnh tiếp theo.

### 9.3 Duyệt kết quả y khoa

```bash
.venv/bin/python main.py review --job-id JOB_ID
```

Chương trình hiển thị các lựa chọn:

1. Duyệt để đăng Facebook sau.
2. Từ chối.
3. Sửa Clinical Factors rồi chạy lại CDHA.
4. Chạy lại Ollama.
5. Chạy lại CDHA.
6. Mở thư mục screenshot.
7. Dừng và duyệt sau.

Hãy kiểm tra nội dung, dữ liệu cá nhân, kết quả CDHA và ảnh trước khi chọn duyệt.

### 9.4 Chuẩn bị, xác nhận và hoàn tất publish

```bash
.venv/bin/python main.py worker --once
.venv/bin/python main.py status --job-id JOB_ID
.venv/bin/python main.py confirm-publish --job-id JOB_ID
.venv/bin/python main.py worker --once
```

Worker chuẩn bị composer rồi dừng tại `FACEBOOK_WAITING_FOR_MANUAL_REVIEW`. `confirm-publish` yêu cầu nhập đúng `PUBLISH JOB_ID`; không có tùy chọn nào bỏ qua cổng này.

## 10. Lệnh stage-only legacy không còn được chạy

Các flag `--reel-url`, `--resume-job`, `--review-job`, `--retry-job`,
`--cancel-job` và `--continue-approved-job` chỉ còn là wrapper tương thích: chúng
in cảnh báo deprecation rồi gọi đúng official use case. Không dùng chúng cho quy
trình mới.

`--download-reel` không có ánh xạ an toàn và trả exit code 2. Dùng `create-job` + `worker`.

```bash
# Deprecated; không thực thi
```

`--process-cdha` không có ánh xạ an toàn và trả exit code 2. Dùng `resume`.

```bash
# Deprecated; không thực thi
```

Không còn lệnh stage-specific để chuẩn bị hoặc hoàn tất Facebook. Dùng `resume`, `worker`, `status` và `confirm-publish` theo trạng thái đã persist.

## 11. Cách B — chạy orchestrator và worker

Cách này cần hai terminal; bảng hàng đợi và bảng workflow dùng chung file `DATABASE_PATH` (mặc định `data/jobs.sqlite3`).

### Terminal 1: chạy worker

```bash
cd /media/nguyen-son-minh/p5/MinhDang
.venv/bin/python main.py worker
```

Giữ terminal này chạy. Dừng worker an toàn bằng `Ctrl+C`.

Worker sẽ tự kết nối hoặc khởi động Chrome CDP theo cấu hình. Không cần chạy script mở browser riêng trong trường hợp bình thường.

### Terminal 2: chạy orchestrator và tạo job

```bash
cd /media/nguyen-son-minh/p5/MinhDang
.venv/bin/python main.py orchestrator
```

Tạo job từ terminal thứ ba (hoặc trước khi chạy hai tiến trình):

```bash
.venv/bin/python main.py create-job --url "https://www.facebook.com/reel/REEL_ID"
```

Orchestrator chỉ:

1. Đọc trạng thái job đã lưu.
2. Tạo queue item duy nhất cho trạng thái có thể chạy tiếp.
3. Không mở browser, tải file, chạy AI, CDHA hay Facebook.

Worker claim một queue item, giữ lease/heartbeat và browser lock, rồi gọi workflow use case. Các wrapper cũ vẫn delegate được nhưng không còn là đường chạy chính thức.

Các lệnh theo dõi/tiếp tục:

```bash
.venv/bin/python main.py status --job-id JOB_ID
.venv/bin/python main.py resume --job-id JOB_ID
.venv/bin/python main.py retry --job-id JOB_ID
.venv/bin/python main.py review --job-id JOB_ID
.venv/bin/python main.py confirm-publish --job-id JOB_ID
```

## 12. Công cụ quản lý Chrome CDP

Các script này chỉ dùng khi cần quản lý browser thủ công:

```bash
./scripts/start_facebook_browser.sh
./scripts/check_facebook_browser.sh
./scripts/stop_facebook_browser.sh
```

Không mở thêm Chrome bằng cùng `runtime/chrome_profiles/cdha_automation` trong khi worker hoặc pipeline đang dùng profile đó. Nếu cổng `9222` thuộc về một tiến trình không đúng profile dự án, browser manager sẽ từ chối kết nối để tránh dùng nhầm session.

## 13. Theo dõi và khôi phục job

Liệt kê job:

```bash
.venv/bin/python main.py --list-jobs
```

Liệt kê job có thể tiếp tục:

```bash
.venv/bin/python main.py --list-resumable-jobs
```

Xem chi tiết và event của một job:

```bash
.venv/bin/python main.py status --job-id JOB_ID
```

Tiếp tục job:

```bash
.venv/bin/python main.py resume --job-id JOB_ID
```

Chạy lại đúng bước bị lỗi:

```bash
.venv/bin/python main.py retry --job-id JOB_ID
```

Hủy job nhưng giữ artifact:

```bash
.venv/bin/python main.py cancel --job-id JOB_ID
```

Sao lưu database:

```bash
.venv/bin/python main.py --backup-db
```

## 14. Dữ liệu và log quan trọng

- Database pipeline: `data/jobs.sqlite3`.
- Database queue của worker: dùng chung `DATABASE_PATH` (mặc định `data/jobs.sqlite3`).
- Artifact theo job: `data/jobs/<JOB_ID>/`.
- Video worker: `runtime/downloads/facebook/` hoặc artifact được adapter lưu theo job.
- Download legacy: `app/infrastructure/legacy/dowloadReelFB/downloads/`.
- Log ứng dụng: `logs/`.
- Chẩn đoán browser: `runtime/diagnostics/` hoặc `data/diagnostics/` tùy luồng.
- Chrome profile chính thức: `runtime/chrome_profiles/cdha_automation`.

## 15. Lỗi thường gặp

### Cookie Facebook không hợp lệ

- Chạy `.venv/bin/python main.py config` và xem `cookie_status`.
- Chỉ cập nhật path do `FACEBOOK_COOKIE_FILE` chỉ định.
- File phải là Netscape cookies; nội dung không được in vào log.
- Không đổi tên hoặc fallback sang `Cookie.txt`/cookie legacy.

### `Virtual environment not found`

Tạo `.venv` và cài dependency theo bước 4. Các shell script cố định Python tại `.venv/bin/python`.

### `Playwright is not installed`

```bash
.venv/bin/python -m pip install -r requirements.txt
```

### Không tìm thấy Chrome

Cập nhật `CHROME_EXECUTABLE_FALLBACK` và `FACEBOOK_CHROME_EXECUTABLE` trong `.env` bằng đường dẫn file Chrome thực tế.

### Ollama không kết nối được hoặc model để trống

```bash
ollama serve
ollama list
```

Đảm bảo `OLLAMA_BASE_URL` đúng và `OLLAMA_MODEL` trùng tên model trong `ollama list`.

### Không có frame hoặc báo thiếu `ffmpeg`

Cài `ffmpeg`, hoặc tắt chức năng nếu chỉ muốn phân tích text:

```dotenv
FRAME_EXTRACTION_ENABLED=false
```

### Profile hoặc browser đang bị khóa

- Dừng worker/pipeline còn chạy bằng `Ctrl+C`.
- Kiểm tra browser bằng `./scripts/check_facebook_browser.sh`.
- Chỉ dùng `./scripts/stop_facebook_browser.sh` khi chắc chắn muốn dừng Chrome CDP của dự án.
- Không tự xóa lock hoặc profile khi chưa xác minh tiến trình sở hữu.

### Job lỗi giữa chừng

```bash
.venv/bin/python main.py status --job-id JOB_ID
.venv/bin/python main.py retry --job-id JOB_ID
```

Nếu job không ở trạng thái retry được, dùng `python main.py resume --job-id JOB_ID` và đọc thông báo `pending_manual_action`.

## 16. Quy trình khuyến nghị cho lần chạy đầu tiên

Chạy lần lượt:

```bash
cd /media/nguyen-son-minh/p5/MinhDang
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --check-config
./scripts/run_worker.sh --preflight-only
.venv/bin/python main.py --login-setup
.venv/bin/python main.py --facebook-login-setup
.venv/bin/python main.py worker --preflight-only
.venv/bin/python main.py create-job --url "https://www.facebook.com/reel/REEL_ID"
.venv/bin/python main.py orchestrator --once
.venv/bin/python main.py worker --once
.venv/bin/python main.py review --job-id JOB_ID
.venv/bin/python main.py worker --once
.venv/bin/python main.py status --job-id JOB_ID
.venv/bin/python main.py confirm-publish --job-id JOB_ID
.venv/bin/python main.py worker --once
```

Không thay `JOB_ID` cho đến khi lệnh chạy Reel in ra ID thật. Luôn đọc kết quả review trước khi cho phép đăng Facebook.
