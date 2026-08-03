# 📄 Hướng dẫn Module Thu Thập Tiêu Đề Bài Viết Facebook Page

Module này **bổ sung** vào dự án AutoFacebook hiện có, **không thay đổi** bất kỳ chức năng nào đang hoạt động (chia sẻ bài, tham gia nhóm, VISIT_LIKE, JOIN_BY_LIST, ...).

---

## 📁 Cấu trúc mới được thêm vào

```text
crawl_page.py                    # Entry point chạy độc lập
src/
├── common/
│   ├── config.py                # Đọc biến môi trường (tiền tố FACEBOOK_*)
│   ├── hashing.py               # SHA-256, dedup, normalize URL
│   └── logging_setup.py         # Log tự ẩn token/cookie/password
├── facebook/
│   ├── graph_client.py          # Meta Graph API client
│   ├── browser_client.py        # Selenium fallback (tái dùng venv)
│   ├── page_crawler.py          # Orchestrator: chọn API/Browser
│   └── post_parser.py           # Trích original_heading, derived_title
└── exporters/
    ├── csv_exporter.py          # Xuất UTF-8 BOM CSV
    └── json_exporter.py         # Xuất JSON đầy đủ
output/                          # File kết quả
data/crawl_state/                # Trạng thái resume
tests/
├── fixtures/facebook_posts.json # Dữ liệu mock
└── test_crawler_unit.py         # 53 unit tests
.env.example                     # Mẫu biến môi trường mới
```

---

## ⚙️ Cấu hình

### Bước 1: Thêm biến môi trường vào `.env`

```env
# Phương án 1: Meta Graph API (ưu tiên)
FACEBOOK_PAGE_ID=robolearnai
FACEBOOK_ACCESS_TOKEN=EAA...your_token_here
FACEBOOK_GRAPH_API_VERSION=v21.0

# Phương án 2: Browser (tự động dùng nếu không có token)
FACEBOOK_HEADLESS=true
FACEBOOK_SCROLL_LIMIT=50

# Nguồn và output
FACEBOOK_SOURCE_PAGE_URL=https://www.facebook.com/robolearnai
FACEBOOK_MAX_POSTS=100
OUTPUT_DIRECTORY=output
```

> **Lưu ý:** Các biến cũ (`MODE`, `HEADLESS`, `TASK4JOIN_CSV`, v.v.) **không bị thay đổi**.

### Bước 2: Lấy Access Token (nếu dùng Graph API)

1. Truy cập: https://developers.facebook.com/tools/explorer/
2. Chọn App của bạn.
3. Thêm quyền: `pages_read_engagement`, `pages_show_list`.
4. Generate Token → copy vào `FACEBOOK_ACCESS_TOKEN` trong `.env`.

---

## 🚀 Cách chạy

### Cách 1: Từ menu `run.sh` (khuyến nghị)

```bash
./run.sh
# Chọn số tương ứng với: crawl_page.py
```

### Cách 2: CLI trực tiếp

```bash
source venv/bin/activate

# Dùng cấu hình từ .env
python crawl_page.py

# Hoặc truyền tham số
python crawl_page.py \
  --page-url "https://www.facebook.com/robolearnai" \
  --max-posts 100 \
  --output "output/robolearnai_titles.csv"

# Bắt buộc dùng browser thay Graph API
python crawl_page.py --browser --max-posts 50

# Lấy bài từ ngày cụ thể
python crawl_page.py --since 2024-01-01 --until 2024-12-31
```

---

## 📊 File kết quả

| File | Mô tả |
|------|-------|
| `output/robolearnai_titles.csv` | Danh sách tiêu đề, UTF-8 BOM, mở được trong Excel |
| `output/robolearnai_posts.json` | Dữ liệu đầy đủ từng bài (JSON array) |
| `output/robolearnai_crawl_summary.json` | Thống kê lần crawl gần nhất |

### Cột CSV

| Cột | Ý nghĩa |
|-----|---------|
| `index` | Số thứ tự |
| `source_page` | `robolearnai` |
| `post_id` | ID bài viết từ Facebook |
| `post_url` | URL trực tiếp bài viết |
| `created_time` | Ngày đăng (ISO 8601) |
| `original_heading` | Dòng đầu tiên gốc (tối đa 250 ký tự) |
| `derived_title` | Tiêu đề rút gọn (40–120 ký tự, không dùng AI) |
| `content_preview` | Tối đa 300 ký tự |
| `post_type` | TEXT \| IMAGE \| VIDEO \| REEL \| LINK \| SHARED_POST \| UNKNOWN |
| `has_image` | true/false |
| `has_video` | true/false |
| `external_url` | URL ngoài (nếu có) |
| `content_hash` | SHA-256 để phát hiện trùng lặp |
| `crawl_method` | GRAPH_API hoặc BROWSER |
| `crawled_at` | Thời điểm thu thập |
| `crawl_status` | SUCCESS \| PARTIAL \| FAILED |
| `error_message` | Thông báo lỗi (nếu có) |

---

## 🔄 Tiếp tục crawl (Resume)

Nếu tiến trình bị dừng giữa chừng, lần chạy tiếp theo sẽ tự động tiếp tục:

```bash
python crawl_page.py  # tự động resume từ data/crawl_state/robolearnai.json
```

---

## 🧪 Chạy tests

```bash
source venv/bin/activate
python -m pytest tests/test_crawler_unit.py -v

# Kết quả mong đợi: 53 passed in < 1s
```

---

## ❓ Xử lý lỗi phổ biến

| Lỗi | Nguyên nhân | Giải pháp |
|-----|-------------|-----------|
| `ACCESS_TOKEN_MISSING` | Chưa đặt `FACEBOOK_ACCESS_TOKEN` | Thêm token vào `.env` hoặc chạy với `--browser` |
| `LOGIN_REQUIRED` | Browser chưa đăng nhập | Chạy `visit-like-post.py` một lần để tạo Chrome profile với session |
| `RATE_LIMITED` | Gọi API quá nhiều | Tăng `FACEBOOK_REQUEST_DELAY_SECONDS` |
| `PARSING_ERROR` | Bài viết có cấu trúc lạ | Bài được ghi là PARTIAL, quá trình không dừng |

---

## 🛡️ Bảo mật

- Token, cookie, password **không bao giờ** được in ra log.
- Module này **chỉ đọc** dữ liệu công khai.
- **Không tự động đăng lại** nội dung.
- **Không thu thập** thông tin cá nhân người bình luận.
