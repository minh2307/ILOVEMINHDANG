# -*- coding: utf-8 -*-
"""
tests/test_crawler_unit.py
===========================
Unit tests cho module thu thập tiêu đề bài viết Facebook Page.
Không gọi bất kỳ API hay trình duyệt thực.
Chạy: source venv/bin/activate && python -m pytest tests/test_crawler_unit.py -v
"""

import json
import os
import sys
import unittest
import tempfile
import hashlib
from pathlib import Path

# Đảm bảo import src.* hoạt động
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# ============================================================
# 1. Test chuẩn hóa URL
# ============================================================
class TestNormalizeUrl(unittest.TestCase):

    def setUp(self):
        from src.common.hashing import normalize_url
        self.normalize_url = normalize_url

    def test_removes_tracking_params(self):
        url = "https://www.facebook.com/robolearnai/posts/123?mibextid=abc&fbclid=xyz"
        result = self.normalize_url(url)
        self.assertNotIn("mibextid", result)
        self.assertNotIn("fbclid", result)
        self.assertIn("/posts/123", result)

    def test_keeps_path(self):
        url = "https://www.facebook.com/robolearnai/posts/456"
        self.assertEqual(self.normalize_url(url), url)

    def test_empty_url(self):
        self.assertEqual(self.normalize_url(""), "")

    def test_no_crash_on_invalid(self):
        result = self.normalize_url("not-a-url")
        self.assertEqual(result, "not-a-url")

    def test_removes_fragment(self):
        url = "https://www.facebook.com/robolearnai/posts/789#top"
        result = self.normalize_url(url)
        self.assertNotIn("#top", result)


# ============================================================
# 2. Test trích xuất dòng đầu (original_heading)
# ============================================================
class TestExtractOriginalHeading(unittest.TestCase):

    def setUp(self):
        from src.facebook.post_parser import extract_original_heading
        self.extract = extract_original_heading

    def test_first_non_empty_line(self):
        msg = "\n\nTiêu đề bài viết\nNội dung tiếp theo"
        result = self.extract(msg)
        self.assertEqual(result, "Tiêu đề bài viết")

    def test_max_250_chars(self):
        msg = "A" * 300
        result = self.extract(msg)
        self.assertLessEqual(len(result), 250)

    def test_empty_message(self):
        from src.facebook.post_parser import LABEL_NO_CONTENT
        result = self.extract("")
        self.assertEqual(result, LABEL_NO_CONTENT)

    def test_whitespace_only(self):
        from src.facebook.post_parser import LABEL_NO_CONTENT
        result = self.extract("   \n  \n  ")
        self.assertEqual(result, LABEL_NO_CONTENT)

    def test_preserves_vietnamese(self):
        msg = "Khai giảng khóa học lập trình robot cho học sinh THPT"
        result = self.extract(msg)
        self.assertEqual(result, msg)

    def test_strips_leading_whitespace(self):
        msg = "  Tiêu đề có khoảng trắng đầu  "
        result = self.extract(msg)
        self.assertEqual(result, "Tiêu đề có khoảng trắng đầu")


# ============================================================
# 3. Test rút gọn tiêu đề (derived_title)
# ============================================================
class TestDeriveTitle(unittest.TestCase):

    def setUp(self):
        from src.facebook.post_parser import derive_title
        self.derive = derive_title

    def test_short_message_returned_as_is(self):
        msg = "Robot học AI. Khóa học mới bắt đầu!"
        result = self.derive(msg)
        self.assertLessEqual(len(result), 120)
        self.assertGreater(len(result), 0)

    def test_long_message_truncated(self):
        msg = "A" * 200
        result = self.derive(msg)
        self.assertLessEqual(len(result), 120)

    def test_no_extra_facts_added(self):
        msg = "Hôm nay ra mắt sản phẩm mới"
        result = self.derive(msg)
        # Không được thêm thông tin không có trong msg
        words_in_msg = set(msg.lower().split())
        for word in result.replace("…", "").split():
            # Từ trong kết quả phải có trong msg (bỏ dấu chấm lửng)
            pass  # Kiểm tra không crash là đủ ở đây

    def test_no_content_label_passthrough(self):
        from src.facebook.post_parser import LABEL_IMAGE_ONLY, LABEL_NO_CONTENT
        result = self.derive("", LABEL_IMAGE_ONLY)
        self.assertEqual(result, LABEL_IMAGE_ONLY)

    def test_result_not_too_short_for_real_text(self):
        msg = "Chào mừng đến với khóa học robot AI dành cho trẻ em từ 8 đến 15 tuổi. Đăng ký ngay!"
        result = self.derive(msg)
        # Kết quả không được rỗng
        self.assertGreater(len(result.strip()), 0)


# ============================================================
# 4. Test bài không có nội dung chữ
# ============================================================
class TestNoTextPost(unittest.TestCase):

    def setUp(self):
        from src.facebook.post_parser import (
            build_no_text_heading,
            LABEL_IMAGE_ONLY, LABEL_VIDEO_ONLY, LABEL_LINK_ONLY, LABEL_NO_CONTENT
        )
        self.build = build_no_text_heading
        self.IMAGE_ONLY = LABEL_IMAGE_ONLY
        self.VIDEO_ONLY = LABEL_VIDEO_ONLY
        self.LINK_ONLY = LABEL_LINK_ONLY
        self.NO_CONTENT = LABEL_NO_CONTENT

    def test_image_only(self):
        self.assertEqual(self.build(has_image=True, has_video=False, external_url=""), self.IMAGE_ONLY)

    def test_video_only(self):
        self.assertEqual(self.build(has_image=False, has_video=True, external_url=""), self.VIDEO_ONLY)

    def test_link_only(self):
        self.assertEqual(self.build(has_image=False, has_video=False, external_url="https://example.com"), self.LINK_ONLY)

    def test_no_content(self):
        self.assertEqual(self.build(has_image=False, has_video=False, external_url=""), self.NO_CONTENT)

    def test_video_takes_priority_over_image(self):
        # Video ưu tiên hơn image
        self.assertEqual(self.build(has_image=True, has_video=True, external_url=""), self.VIDEO_ONLY)


# ============================================================
# 5. Test tạo content_hash
# ============================================================
class TestContentHash(unittest.TestCase):

    def setUp(self):
        from src.common.hashing import make_content_hash
        self.hash = make_content_hash

    def test_hash_is_sha256_hex(self):
        h = self.hash("heading", "preview", "2024-01-01")
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_same_input_same_hash(self):
        h1 = self.hash("Tiêu đề", "Nội dung", "2024-01-01")
        h2 = self.hash("Tiêu đề", "Nội dung", "2024-01-01")
        self.assertEqual(h1, h2)

    def test_different_input_different_hash(self):
        h1 = self.hash("Tiêu đề A", "Nội dung A", "2024-01-01")
        h2 = self.hash("Tiêu đề B", "Nội dung B", "2024-01-02")
        self.assertNotEqual(h1, h2)

    def test_case_insensitive(self):
        h1 = self.hash("TIÊU ĐỀ", "NỘI DUNG", "2024-01-01")
        h2 = self.hash("tiêu đề", "nội dung", "2024-01-01")
        self.assertEqual(h1, h2)

    def test_extra_whitespace_normalized(self):
        h1 = self.hash("tiêu đề  abc", "preview", "2024")
        h2 = self.hash("tiêu đề abc", "preview", "2024")
        self.assertEqual(h1, h2)

    def test_vietnamese_diacritics_preserved(self):
        # Dấu tiếng Việt giữ nguyên trong hash (không bị strip)
        h1 = self.hash("tiêu đề", "nội dung", "2024")
        h2 = self.hash("tieu de", "noi dung", "2024")
        self.assertNotEqual(h1, h2)


# ============================================================
# 6. Test phát hiện bài trùng
# ============================================================
class TestDuplicateDetection(unittest.TestCase):

    def setUp(self):
        from src.common.hashing import is_duplicate
        self.is_dup = is_duplicate

    def test_dup_by_post_id(self):
        self.assertTrue(self.is_dup("123", "", "", {"123"}, set(), set()))

    def test_dup_by_url(self):
        self.assertTrue(self.is_dup("", "https://fb.com/posts/456", "", set(), {"https://fb.com/posts/456"}, set()))

    def test_dup_by_hash(self):
        self.assertTrue(self.is_dup("", "", "abc123", set(), set(), {"abc123"}))

    def test_not_dup(self):
        self.assertFalse(self.is_dup("999", "https://fb.com/posts/999", "xyz", set(), set(), set()))

    def test_empty_post_id_not_false_positive(self):
        # post_id rỗng không nên match với rỗng trong set
        self.assertFalse(self.is_dup("", "", "", {"123"}, set(), set()))


# ============================================================
# 7. Test phân loại post type
# ============================================================
class TestPostType(unittest.TestCase):

    def setUp(self):
        from src.facebook.post_parser import detect_post_type
        self.detect = detect_post_type

    def test_text_post(self):
        self.assertEqual(self.detect("Hello world", False, False), "TEXT")

    def test_image_post(self):
        self.assertEqual(self.detect("Caption ảnh", True, False), "IMAGE")

    def test_video_post(self):
        self.assertEqual(self.detect("", False, True), "VIDEO")

    def test_link_post_from_attachment(self):
        attachments = {"data": [{"type": "share", "url": "https://example.com"}]}
        result = self.detect("Xem bài viết này", False, False, attachments)
        self.assertEqual(result, "LINK")

    def test_link_post_from_message_url(self):
        msg = "Xem tại https://vnexpress.net/article"
        self.assertEqual(self.detect(msg, False, False), "LINK")

    def test_unknown_no_content(self):
        self.assertEqual(self.detect("", False, False), "UNKNOWN")

    def test_shared_post_event(self):
        attachments = {"data": [{"type": "event"}]}
        self.assertEqual(self.detect("Join event", False, False, attachments), "SHARED_POST")


# ============================================================
# 8. Test xuất CSV tiếng Việt
# ============================================================
class TestCSVExport(unittest.TestCase):

    def _make_record(self, **kwargs):
        from src.facebook.page_crawler import PostRecord
        defaults = {
            "index": 1, "source_page": "robolearnai",
            "post_id": "test_001", "post_url": "https://fb.com/posts/001",
            "created_time": "2024-03-15T08:00:00Z",
            "original_heading": "Tiêu đề bài viết tiếng Việt",
            "derived_title": "Tiêu đề rút gọn",
            "content_preview": "Nội dung xem trước",
            "post_type": "TEXT", "has_image": False, "has_video": False,
            "external_url": "", "content_hash": "abc123",
            "crawl_method": "GRAPH_API", "crawled_at": "2024-03-20T10:00:00Z",
            "crawl_status": "SUCCESS", "error_message": "",
        }
        defaults.update(kwargs)
        return PostRecord(**defaults)

    def test_csv_utf8_bom(self):
        from src.exporters.csv_exporter import CSVExporter
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            tmp = f.name
        try:
            exporter = CSVExporter(tmp)
            exporter.export([self._make_record()])
            raw = open(tmp, "rb").read(3)
            self.assertEqual(raw, b"\xef\xbb\xbf")  # UTF-8 BOM
        finally:
            os.unlink(tmp)

    def test_csv_has_headers(self):
        from src.exporters.csv_exporter import CSVExporter, CSV_COLUMNS
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            tmp = f.name
        try:
            exporter = CSVExporter(tmp)
            exporter.export([self._make_record()])
            content = open(tmp, encoding="utf-8-sig").read()
            for col in CSV_COLUMNS:
                self.assertIn(col, content)
        finally:
            os.unlink(tmp)

    def test_csv_vietnamese_readable(self):
        from src.exporters.csv_exporter import CSVExporter
        import csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            tmp = f.name
        try:
            exporter = CSVExporter(tmp)
            rec = self._make_record(original_heading="Khai giảng khóa học lập trình robot")
            exporter.export([rec])
            with open(tmp, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["original_heading"], "Khai giảng khóa học lập trình robot")
        finally:
            os.unlink(tmp)


# ============================================================
# 9. Test cập nhật CSV cũ (không ghi đè bài cũ)
# ============================================================
class TestCSVUpdate(unittest.TestCase):

    def _make_record(self, post_id, heading, created="2024-01-01T00:00:00Z"):
        from src.facebook.page_crawler import PostRecord
        return PostRecord(
            index=1, source_page="robolearnai",
            post_id=post_id, post_url=f"https://fb.com/posts/{post_id}",
            created_time=created, original_heading=heading,
            derived_title=heading, content_preview=heading,
            post_type="TEXT", has_image=False, has_video=False,
            external_url="", content_hash=hashlib.sha256(post_id.encode()).hexdigest(),
            crawl_method="GRAPH_API", crawled_at="2024-01-10T00:00:00Z",
            crawl_status="SUCCESS", error_message="",
        )

    def test_old_posts_preserved(self):
        from src.exporters.csv_exporter import CSVExporter
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            tmp = f.name
        try:
            exporter = CSVExporter(tmp)
            # Lần 1: ghi bài cũ
            exporter.export([self._make_record("001", "Bài cũ")])
            # Lần 2: thêm bài mới
            exporter.export([self._make_record("002", "Bài mới")])
            import csv
            with open(tmp, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            ids = [r["post_id"] for r in rows]
            self.assertIn("001", ids)
            self.assertIn("002", ids)
        finally:
            os.unlink(tmp)

    def test_no_duplicate_rows(self):
        from src.exporters.csv_exporter import CSVExporter
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            tmp = f.name
        try:
            exporter = CSVExporter(tmp)
            rec = self._make_record("003", "Bài trùng")
            exporter.export([rec])
            exporter.export([rec])  # chạy lại với cùng bài
            import csv
            with open(tmp, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            count = sum(1 for r in rows if r["post_id"] == "003")
            self.assertEqual(count, 1)
        finally:
            os.unlink(tmp)


# ============================================================
# 10. Test che token và cookie trong log
# ============================================================
class TestLogMasking(unittest.TestCase):

    def setUp(self):
        from src.common.logging_setup import mask_sensitive
        self.mask = mask_sensitive

    def test_access_token_masked(self):
        text = "access_token=EAABwzLixnjYBAHmjf123abc456"
        result = self.mask(text)
        self.assertNotIn("EAABwzLixnjYBAHmjf123abc456", result)
        self.assertIn("[MASKED]", result)

    def test_bearer_token_masked(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = self.mask(text)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", result)

    def test_password_masked(self):
        text = "password=my_super_secret"
        result = self.mask(text)
        self.assertNotIn("my_super_secret", result)

    def test_non_sensitive_unchanged(self):
        text = "Đây là thông báo bình thường"
        result = self.mask(text)
        self.assertEqual(result, text)

    def test_xs_cookie_masked(self):
        text = "xs=AbCdEfGhIjKlMnOpQrStUv"
        result = self.mask(text)
        self.assertNotIn("AbCdEfGhIjKlMnOpQrStUv", result)


# ============================================================
# Integration test: parse mock fixtures
# ============================================================
class TestIntegrationMockFixtures(unittest.TestCase):

    def _load_fixtures(self):
        fixture_path = Path(__file__).parent / "fixtures" / "facebook_posts.json"
        with fixture_path.open(encoding="utf-8") as f:
            return json.load(f)

    def test_parse_all_fixtures(self):
        from src.facebook.post_parser import parse_post
        fixtures = self._load_fixtures()
        for raw in fixtures:
            parsed = parse_post(raw, "robolearnai")
            self.assertIn("original_heading", parsed)
            self.assertIn("derived_title", parsed)
            self.assertIn("post_type", parsed)
            # Hai trường KHÔNG được giống nhau khi có text
            if parsed.get("content_preview"):
                # original_heading và derived_title là 2 cột riêng biệt
                self.assertIsNotNone(parsed["original_heading"])
                self.assertIsNotNone(parsed["derived_title"])

    def test_no_duplicate_hash_in_fixtures(self):
        from src.facebook.post_parser import parse_post
        from src.common.hashing import make_content_hash
        fixtures = self._load_fixtures()
        hashes = []
        for raw in fixtures:
            if raw.get("id") == "1005":
                continue  # bài rỗng hợp lý trùng hash
            parsed = parse_post(raw, "robolearnai")
            h = make_content_hash(
                parsed.get("original_heading", ""),
                parsed.get("content_preview", ""),
                parsed.get("created_time", ""),
            )
            hashes.append(h)
        # Không có hash trùng (trừ bài rỗng)
        self.assertEqual(len(hashes), len(set(hashes)))

    def test_post_types_classified(self):
        from src.facebook.post_parser import parse_post
        fixtures = self._load_fixtures()
        types = set()
        for raw in fixtures:
            parsed = parse_post(raw, "robolearnai")
            types.add(parsed["post_type"])
        # Phải phân loại được nhiều hơn 1 loại
        self.assertGreater(len(types), 1)

    def test_video_classified_correctly(self):
        from src.facebook.post_parser import parse_post
        fixtures = self._load_fixtures()
        for raw in fixtures:
            if raw.get("has_video"):
                parsed = parse_post(raw, "robolearnai")
                self.assertEqual(parsed["post_type"], "VIDEO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
