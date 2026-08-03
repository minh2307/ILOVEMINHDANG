# 🎯 Universal Facebook Group Auto Poster & Interactor (Tài liệu Hướng dẫn Sử dụng)

Hệ thống tự động hóa đăng bài, tham gia nhóm và tương tác trên Facebook sử dụng **Python**, **Selenium**, và **OpenAI GPT** để giả lập hành vi người dùng tự nhiên. 

Dự án được tối ưu hóa nhằm tăng tỉ lệ tiếp cận, gia nhập nhóm một cách tự động và thông minh, đồng thời hạn chế tối đa nguy cơ bị khóa tài khoản (checkpoint) nhờ tích hợp giả lập di chuyển chuột và các hành vi tương tác thực tế.

---

## 🚀 Các Tính Năng Chính

### 1. Đa dạng Chế độ Hoạt động (Modes)
Dự án hỗ trợ 5 chế độ hoạt động chính, cấu hình trực tiếp qua biến `MODE` trong tệp `.env`:
*   **`POST_ONLY`**: Chỉ tự động đăng bài viết (kèm hình ảnh) lên danh sách các nhóm được chỉ định trong file CSV.
*   **`INTERACT_ONLY`**: Chỉ đi tương tác tự nhiên trong nhóm (cuộn trang, xem các tab Giới thiệu/Thành viên/Ảnh, bấm Like ngẫu nhiên) mà không đăng bài.
*   **`POST_PLUS_INTERACT`**: Kết hợp đăng bài viết mới và sau đó thực hiện tương tác tự nhiên với feed của nhóm.
*   **`JOIN_BY_LIST`**: 
    *   Tự động gửi yêu cầu tham gia các nhóm từ danh sách trong `data/task4join.csv`.
    *   Tích hợp **OpenAI API (GPT)** để đọc câu hỏi duyệt nhóm của Admin, tự động soạn câu trả lời phù hợp theo ngữ cảnh/lĩnh vực (Domain) và tự động đồng ý với các điều khoản nhóm.
    *   Cập nhật trạng thái tham gia (`request_sent`, `already_member`, `joined`, hoặc lỗi) trực tiếp vào cột **Status** của file CSV.
*   **`VISIT_LIKE`**:
    *   Đọc danh sách nhóm từ file CSV (`VISIT_LIKE_CSV` hoặc mặc định là `data/task4join.csv`).
    *   Lọc các nhóm có trạng thái hợp lệ (`request_sent`, `already_member`, `joined`).
    *   Xáo trộn ngẫu nhiên thứ tự danh sách nhóm để tránh trùng lặp thứ tự truy cập.
    *   Thực hiện đăng bài ngẫu nhiên theo xác suất được cấu hình (`VISIT_LIKE_POST_PROBABILITY`).
    *   Tự động cuộn trang và bấm Like/Reaction (Love, Like) cho 3-5 bài viết đầu tiên trên feed của nhóm để tăng uy tín tài khoản.
    *   Cập nhật thời gian hoạt động gần nhất vào cột **LastUpdated** trong file CSV.

### 2. Giả lập Hành vi Người dùng Tự nhiên (Human-like Interactions)
*   **Human Mouse Movement**: Di chuyển trỏ chuột ngẫu nhiên và mượt mà tới các phần tử đích với tốc độ ngẫu nhiên thay vì click lập tức.
*   **Lexical Editor Support**: Nhập nội dung bài viết bằng cách kết hợp tiêm ký tự qua JS và giả lập tổ hợp phím Enter thực tế để giữ nguyên định dạng xuống dòng của Facebook.
*   **Khay Reaction Thực**: Rê chuột lên nút Like để hiển thị khay cảm xúc và ưu tiên thả tim (`Love`) hoặc thích (`Like`) ngẫu nhiên.
*   **Thời gian Chờ Ngẫu nhiên**: Tích hợp các khoảng nghỉ (`delay_range`) động giữa các nhóm và các hành vi tương tác trong nhóm nhằm tránh thuật toán quét spam của Facebook.

### 3. Quản lý Phiên Đăng nhập & An toàn Hệ thống
*   **Persistent Chrome Profile**: Lưu trữ và sử dụng lại bộ nhớ đệm (Chrome Profile) tại thư mục cố định nhằm duy trì trạng thái đăng nhập Facebook mà không cần đăng nhập lại nhiều lần.
*   **Cơ chế Tránh Xung đột**: Tự động tạo tệp khóa `.profile.lock`. Nếu phát hiện phiên làm việc khác đang chạy trên profile chính, hệ thống sẽ tự động khởi tạo một profile phụ theo ID tiến trình (PID) nếu được cho phép (`FB_POSTER_PROFILE_SECONDARY=1`).
*   **Chống Đăng Trùng (Dedup API)**: Tích hợp API kiểm tra trùng lặp (`autopost.php`) để so khớp mã băm SHA-256 của nội dung, đảm bảo không đăng lặp bài viết trên cùng một nhóm trong khoảng thời gian nhất định.
*   **Chế độ Headless**: Hỗ trợ chạy trình duyệt ẩn danh (không hiện giao diện đồ họa) để triển khai trên các máy chủ/VPS Linux không có màn hình.

---

## 📁 Cấu trúc Thư mục

```text
├── visit-like-post.py   # Script Python chính chứa toàn bộ logic tự động hóa
├── Start.sh             # Script khởi động tự động kích hoạt venv và chạy chương trình
├── .env                 # File cấu hình môi trường và các tham số vận hành
├── data/                # Thư mục lưu trữ dữ liệu đầu vào và trạng thái
│   ├── task4join.csv    # Danh sách nhóm mẫu phục vụ chế độ JOIN_BY_LIST / VISIT_LIKE
│   ├── selected_recruitment_groups.csv  # Danh sách nhóm mặc định cho chế độ đăng bài
│   ├── post_content.txt # Nội dung bài đăng mặc định
│   └── post1.png        # Hình ảnh mặc định đính kèm bài viết
├── posts/               # Thư mục chứa bài viết nâng cao (cần tự tạo nếu dùng tính năng này)
│   ├── contents.txt     # Danh sách các nội dung bài viết ngẫu nhiên (phân tách bằng #1:, #2:, ...)
│   └── *.png, *.jpg...  # Các hình ảnh ngẫu nhiên để đính kèm bài đăng
└── venv/                # Môi trường ảo Python chứa các thư viện cài đặt
```

---

## ⚙️ Hướng dẫn Cài đặt & Cấu hình

### 1. Yêu cầu Hệ thống
*   Hệ điều hành: Linux (Ubuntu/Debian) hoặc macOS/Windows.
*   Đã cài đặt **Python 3.8+** và **pip**.
*   Đã cài đặt trình duyệt **Google Chrome** phiên bản mới nhất.

### 2. Chuẩn bị Môi trường
Mở Terminal tại thư mục dự án và thực hiện các bước sau:

1.  **Tạo môi trường ảo Python:**
    ```bash
    python3 -m venv venv
    ```
2.  **Kích hoạt môi trường ảo:**
    *   Trên Linux/macOS:
        ```bash
        source venv/bin/activate
        ```
    *   Trên Windows:
        ```cmd
        venv\Scripts\activate
        ```
3.  **Cài đặt các thư viện cần thiết:**
    ```bash
    pip install selenium webdriver-manager requests python-dotenv
    ```

### 3. Cấu hình File `.env`
Tạo hoặc chỉnh sửa file `.env` tại thư mục gốc của dự án. Dưới đây là bảng giải thích chi tiết các tham số:

| Tên biến | Kiểu dữ liệu | Giá trị mặc định | Mô tả chi tiết |
| :--- | :--- | :--- | :--- |
| **`MODE`** | String | `VISIT_LIKE` | Chế độ chạy: `POST_ONLY` \| `INTERACT_ONLY` \| `POST_PLUS_INTERACT` \| `JOIN_BY_LIST` \| `VISIT_LIKE` |
| **`VISIT_LIKE_CSV`** | Path | `data/task4join.csv` | File CSV chứa danh sách các nhóm phục vụ cho chế độ `VISIT_LIKE`. |
| **`OPENAI_API_KEY`** | String | *(Bỏ trống)* | API Key OpenAI (Dùng để trả lời câu hỏi tự động khi xin gia nhập nhóm). |
| **`OPENAI_MODEL`** | String | `gpt-4o-mini` | Model OpenAI dùng để xử lý câu hỏi. |
| **`DEFAULT_DOMAIN`** | String | `"y tế"` | Định hướng lĩnh vực chuyên môn để AI dựa vào khi trả lời câu hỏi của Admin. |
| **`TASK4JOIN_CSV`** | Path | `data/task4join.csv` | Đường dẫn tới danh sách nhóm đầu vào cho chế độ `JOIN_BY_LIST`. |
| **`DEFAULT_GROUPS_CSV`** | Path | `data/selected_recruitment_groups.csv` | Danh sách nhóm mặc định cho các chế độ đăng bài thông thường. |
| **`DEFAULT_CONTENT_TXT`** | Path | `data/post_content.txt` | Nội dung bài viết mặc định khi không tìm thấy danh sách bài viết nâng cao. |
| **`DEFAULT_IMAGE_PATH`** | Path | `data/post1.png` | Hình ảnh mặc định đính kèm bài viết khi đăng bài. |
| **`DEDUP_API_BASE`** | URL | `https://upload.cdha.ai/api/autopost.php` | API server SQLite để kiểm tra trùng lặp bài đăng. |
| **`DEDUP_API_KEY`** | String | `chuoi-bi-mat-dai-ngu-nhien` | Khóa API bảo mật kết nối tới Dedup Server. |
| **`FB_POSTER_PROFILE`** | Path | `~/.cache/auto-join-ai/chrome-profile` | Thư mục lưu Chrome Profile nhằm duy trì đăng nhập Facebook. |
| **`FB_POSTER_PROFILE_SECONDARY`** | Boolean | `1` | `1`: Cho phép khởi tạo profile phụ theo PID khi profile chính bị lock. `0`: Dừng chương trình. |
| **`HEADLESS`** | Boolean | `0` | `1`: Chạy ẩn trình duyệt (không hiện UI). `0`: Hiển thị trình duyệt trực quan. |
| **`VISIT_LIKE_ENABLE_POST`** | Boolean | `true` | Có cho phép đăng bài viết khi đang thực hiện chế độ `VISIT_LIKE` hay không. |
| **`VISIT_LIKE_POST_PROBABILITY`** | Float | `1.0` | Xác suất đăng bài ngẫu nhiên (từ `0.0` đến `1.0`) trong chế độ `VISIT_LIKE`. |
| **`VISIT_LIKE_COMMENTS`** | Boolean | `false` | Có bấm Like các bình luận trong nhóm hay không. |

---

## 📊 Định dạng Dữ liệu Đầu vào (Input Specification)

### 1. Định dạng File CSV nhóm (`task4join.csv` hoặc `selected_recruitment_groups.csv`)
File CSV cần có tiêu đề (Header) rõ ràng. Định dạng khuyên dùng:
```csv
No,Name,URL,Status,Public,Member,Post,LastUpdated
1,Medical Case Consultation,https://www.facebook.com/groups/medclick/,request_sent,Riêng tư,609000,10+,2025-10-31 17:49:09
2,Medical Lab Technology MLT,https://www.facebook.com/groups/315866622158163/,already_member,Công khai,400000,50+,2025-11-01 21:13:42
```
*   **Status**: Hệ thống sẽ tự động đọc trạng thái này để ra quyết định và ghi đè trạng thái mới khi chạy chế độ `JOIN_BY_LIST` hoặc `VISIT_LIKE`.
*   Các giá trị trạng thái chuẩn gồm: `request_sent` (Đã gửi yêu cầu), `already_member` (Đã là thành viên), `joined` (Đã tham gia trực tiếp).

### 2. Định dạng File Nội dung Bài đăng Nâng cao (`posts/contents.txt`)
Nếu bạn cấu hình `POSTS_CONTENTS_FILE`, hệ thống sẽ lấy ngẫu nhiên các bài đăng được định dạng theo cú pháp sau:
```text
#1:
Chào các bác sĩ và kỹ thuật viên hình ảnh!
Đây là nội dung bài đăng số 1...

#2:
Radiology Update:
Đây là nội dung bài đăng số 2...
```
*Mỗi bài đăng bắt đầu bằng ký hiệu `#1:`, `#2:`, ... Hệ thống sẽ tự tách nội dung và lấy ngẫu nhiên để đăng bài.*

---

## 🏃 Hướng dẫn Sử dụng

### Cách 1: Sử dụng công cụ tự động `Start.sh` (Khuyên dùng trên Linux)
Cung cấp quyền thực thi cho file shell và chạy:
```bash
chmod +x Start.sh
./Start.sh
```
Hệ thống sẽ tự động thực hiện các thao tác:
1.  Nạp cấu hình từ tệp `.env`.
2.  Kiểm tra Python và sự tồn tại của môi trường ảo (`venv`).
3.  Tìm kiếm tất cả các file script `.py` có sẵn trong thư mục hiện tại.
4.  Hiển thị menu trực quan cho phép bạn nhập số tương ứng để khởi chạy (nhấn `Enter` để chạy file mặc định).

### Cách 2: Chạy trực tiếp bằng Python
Sau khi đã kích hoạt môi trường ảo `venv`:
```bash
python3 visit-like-post.py
```

---

## ⚠️ Lưu ý Quan trọng & Khắc phục Sự cố

### 1. Đăng nhập Facebook Lần đầu tiên
*   Trong lần đầu chạy chương trình, hãy đặt `HEADLESS=0` trong file `.env` để trình duyệt hiển thị giao diện đồ họa.
*   Khi Chrome mở ra, tiến hành đăng nhập tài khoản Facebook của bạn thủ công và vượt qua xác thực 2FA (nếu có).
*   Chrome Profile tại thư mục `FB_POSTER_PROFILE` sẽ ghi nhớ phiên đăng nhập. Các lần chạy sau đó hệ thống sẽ tự động truy cập mà không yêu cầu đăng nhập lại.

### 2. Lỗi khóa Profile (Profile Locked)
*   **Triệu chứng**: Thông báo lỗi Chrome Profile đang được sử dụng bởi một tiến trình khác.
*   **Nguyên nhân**: File khóa `.profile.lock` chưa được giải phóng hoặc có tiến trình Selenium chạy ẩn chưa tắt hẳn.
*   **Khắc phục**: 
    1. Đóng toàn bộ các tab Chrome được mở bởi Selenium.
    2. Chạy lệnh: `pkill -f chrome` hoặc `pkill -f chromedriver` trên Linux để giải phóng tài nguyên.
    3. Cấu hình `FB_POSTER_PROFILE_SECONDARY=1` trong `.env` để tự động tạo profile phụ tránh xung đột.

### 3. Tích hợp AI & Trả lời câu hỏi Admin
*   Khi chạy chế độ `JOIN_BY_LIST`, hãy chắc chắn đã điền `OPENAI_API_KEY` hợp lệ vào `.env`.
*   Nếu không có khóa API OpenAI, hệ thống sẽ sử dụng câu trả lời mặc định bằng tiếng Anh: *"I am interested in the group topic and agree to abide by the rules. Thank you."*

### 4. Giới hạn Tần suất (Anti-Spam)
*   Mặc định hệ thống sử dụng khoảng nghỉ ngẫu nhiên `(3, 10)` giây giữa các nhóm để demo/kiểm tra nhanh.
*   **Khuyến cáo**: Để bảo vệ tài khoản Facebook khỏi bị checkpoint hoặc chặn tính năng đăng bài, bạn nên tăng khoảng delay này trong mã nguồn (ví dụ: `(60, 180)` giây hoặc hơn) nếu thực hiện đăng bài liên tục trên số lượng lớn nhóm.

---
*Chúc bạn vận hành dự án thành công và đạt hiệu quả tương tác tối đa!*
