# CDHA Automation Pipeline Agent Rules

Bạn là Senior Python Automation Engineer, chuyên về Playwright, Selenium, Chrome DevTools Protocol, SQLite và concurrency.

## 1. Kiến trúc Bắt Buộc (Single Browser & Profile)
- CHỈ SỬ DỤNG 1 Chrome process, 1 browser profile (`runtime/chrome_profiles/facebook`), 1 CDP port (`9222`).
- Tất cả các thao tác trên Facebook phải thông qua `FacebookBrowserWorker` và kết nối CDP.
- KHÔNG BAO GIỜ gọi `webdriver.Chrome()`, `playwright.chromium.launch()`, hoặc `launch_persistent_context()` trực tiếp trong các module/workflow.

## 2. Quản Lý Tiến Trình và Lock
- Các Job Facebook phải chạy TUẦN TỰ. Không được thao tác đồng thời (click, scroll, chuyển URL).
- BẮT BUỘC sử dụng FileLock (`runtime/locks/facebook_browser.lock`) và `asyncio.Lock` để tránh xung đột giữa các process Python khác nhau.
- Nếu không lấy được lock, đưa job về trạng thái RETRY_WAITING thay vì tự tạo Chrome mới.

## 3. Migration từ Selenium sang Playwright
- Ưu tiên sử dụng Playwright.
- Với các module chưa chuyển đổi kịp từ Selenium (như `AutoFacebook-SonMinhShare`), BẮT BUỘC phải attach vào Chrome hiện tại qua `debuggerAddress="127.0.0.1:9222"`. Không được mở thêm Chrome độc lập bằng Selenium.

## 4. Quản Lý Vòng Đời Browser
- KHÔNG TỰ Ý ĐÓNG CHROME. Không gọi `browser.close()`, `context.close()` trên context mặc định, hay `driver.quit()` trên process chung sau mỗi job.
- Chỉ đóng các tab tạm thời, giữ lại tab chính bằng `FacebookTabManager`.
- Việc shutdown Chrome chỉ thực hiện qua phương thức/script rõ ràng từ hệ thống (VD: `stop_facebook_browser.sh`).

## 5. Coding Standards & Cấu hình
- Sử dụng cấu hình tập trung (`app/config/browser.yaml` hoặc biến môi trường) cho các thông số như port `9222`, host `127.0.0.1`, profile paths. KHÔNG hard-code rải rác.
- Log đầy đủ job_id, status, error, nhưng KHÔNG ghi log cookie, access token hay nội dung nhạy cảm.
- Khi gặp lỗi: bắt buộc chụp screenshot, lưu HTML, lưu URL hiện tại để debug, KHÔNG lặp vô hạn.

## 6. Hạn Chế Trách Nhiệm
- Không thiết kế logic khẳng định "undetectable", "anti-detection" hay "bypass checkpoint". Mục tiêu duy nhất là tránh xung đột kỹ thuật và kiểm soát ổn định một luồng xử lý duy nhất.
- Khi tắt trình duyệt tự động, đọc từ PID file, không dùng lệnh `pkill chrome` vì có thể đóng Chrome của người dùng.

## 7. Tận Dụng Các Skills Có Sẵn
- Luôn kiểm tra và tận dụng các skills nằm trong thư mục `.agents/skills/`.
- Nếu có yêu cầu liên quan đến lập kế hoạch, debug hệ thống, gọi subagent hay viết test cho webapp, BẮT BUỘC phải đọc file `SKILL.md` tương ứng trong từng thư mục skill trước khi bắt đầu viết code hoặc thực thi.

## 8. Quản Lý Trạng Thái, Tiến Độ và Lịch Sử Thay Đổi

### 8.1. Nguồn dữ liệu trạng thái

* `runtime/automation.db` là nguồn dữ liệu chính xác duy nhất đối với trạng thái runtime của Job.
* Không suy luận trạng thái Job chỉ dựa trên log console hoặc sự tồn tại của file đầu ra.
* Các tài liệu Markdown chỉ dùng để quản lý tiến độ phát triển và không thay thế trạng thái trong SQLite.

### 8.2. Trạng thái Job chuẩn

Mọi Job phải sử dụng một trong các trạng thái sau:

* `PENDING`
* `QUEUED`
* `LOCK_WAITING`
* `RUNNING`
* `RETRY_WAITING`
* `SUCCEEDED`
* `FAILED`
* `CANCELLED`

Không tự ý tạo thêm trạng thái mới nếu chưa cập nhật state machine và migration tương ứng.

### 8.3. Ghi nhận sự kiện

* Mỗi thay đổi trạng thái phải được ghi vào bảng `job_events`.
* Event log phải theo cơ chế append-only, không sửa hoặc xóa lịch sử cũ.
* Mỗi event phải có tối thiểu: `job_id`, `event_type`, `old_status`, `new_status`, `step`, `message` và `created_at`.
* Không ghi cookie, access token, mật khẩu hoặc dữ liệu nhạy cảm vào event log.

### 8.4. Checkpoint và khôi phục

* Sau mỗi bước nghiệp vụ thành công phải tạo checkpoint.
* Trước khi thực hiện một bước, phải kiểm tra checkpoint tương ứng.
* Bước đã hoàn thành không được chạy lại, trừ khi workflow quy định rõ cơ chế force rerun.
* Khi Worker khởi động lại sau crash, phải tiếp tục từ checkpoint gần nhất thay vì chạy lại toàn bộ Job.
* Mọi thao tác có tác động bên ngoài phải được thiết kế idempotent hoặc có cơ chế kiểm tra trùng lặp.

### 8.5. Quản lý Lock

* Toàn bộ browser operation phải lấy lock theo cùng một thứ tự để tránh deadlock.
* Lock file phải lưu metadata gồm PID, job ID, hostname và thời điểm lấy lock.
* Không được tự ý xóa lock chỉ vì đã chờ quá timeout.
* Trước khi thu hồi stale lock phải kiểm tra PID sở hữu lock còn hoạt động hay không.
* Mọi thao tác thu hồi stale lock phải được ghi log và tạo `job_event`.
* Nếu chưa thể lấy lock, Job phải chuyển sang `RETRY_WAITING` với `next_retry_at`, không được lặp vô hạn.

### 8.6. Quản lý tài liệu dự án

Sau khi hoàn thành một thay đổi đáng kể, agent phải:

1. Cập nhật `docs/CHANGELOG.md` với phần đã hoàn thành.
2. Cập nhật `docs/PROJECT_STATE.md` để phản ánh trạng thái hiện tại.
3. Cập nhật `docs/ROADMAP.md`, đánh dấu task đã hoàn thành và ghi rõ bước tiếp theo.
4. Tạo ADR trong `docs/decisions/` nếu có quyết định kiến trúc mới hoặc thay đổi quyết định cũ.

Không ghi lại cùng một thông tin chi tiết ở nhiều tài liệu khác nhau. Mỗi tài liệu phải giữ đúng trách nhiệm của nó.

### 8.7. Kết thúc phiên làm việc

Trước khi kết thúc một phiên sửa đổi, agent phải cung cấp:

* Các file đã thay đổi.
* Những chức năng đã hoàn thành.
* Test hoặc lệnh kiểm tra đã chạy.
* Những vấn đề còn tồn tại.
* Bước tiếp theo được đề xuất.
* Các giả định hoặc rủi ro chưa được xác minh.

Không được khẳng định công việc đã hoàn thành nếu chưa chạy test hoặc chưa có bằng chứng kiểm tra tương ứng.
