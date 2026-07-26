#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crawl_page.py
=============
Entry point độc lập để thu thập tiêu đề bài viết từ Facebook Page.

Hỗ trợ hai cách gọi:
  1) Từ run.sh → chọn file này trong menu
  2) CLI trực tiếp:
       python crawl_page.py --page-url "https://www.facebook.com/robolearnai" --max-posts 100

Không ảnh hưởng đến visit-like-post.py và các chức năng hiện có.
"""

import argparse
import sys
import os

# Đảm bảo import src.* hoạt động khi chạy từ thư mục gốc dự án
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from src.common.logging_setup import get_logger

log = get_logger("fb_crawler")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Thu thập tiêu đề bài viết từ Facebook Page",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--page-url",
        default=os.getenv("FACEBOOK_SOURCE_PAGE_URL", "https://www.facebook.com/robolearnai"),
        help="URL của Facebook Page cần crawl",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=int(os.getenv("FACEBOOK_MAX_POSTS", "100")),
        help="Số lượng bài tối đa cần lấy",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("OUTPUT_CSV", "output/robolearnai_titles.csv"),
        help="Đường dẫn file CSV đầu ra",
    )
    parser.add_argument(
        "--output-json",
        default=os.getenv("OUTPUT_JSON", "output/robolearnai_posts.json"),
        help="Đường dẫn file JSON đầu ra",
    )
    parser.add_argument(
        "--output-summary",
        default=os.getenv("OUTPUT_SUMMARY", "output/robolearnai_crawl_summary.json"),
        help="Đường dẫn file summary đầu ra",
    )
    parser.add_argument(
        "--since",
        default=os.getenv("FACEBOOK_CRAWL_SINCE", ""),
        help="Lấy bài từ ngày (ISO, ví dụ: 2024-01-01). Bỏ trống = không giới hạn.",
    )
    parser.add_argument(
        "--until",
        default=os.getenv("FACEBOOK_CRAWL_UNTIL", ""),
        help="Lấy bài đến ngày (ISO). Bỏ trống = không giới hạn.",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Bắt buộc dùng browser automation thay vì Graph API",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    log.info("=" * 60)
    log.info("🤖 Facebook Page Crawler — Module Thu Thập Tiêu Đề")
    log.info("   Page URL  : %s", args.page_url)
    log.info("   Max posts  : %s", args.max_posts)
    log.info("   Output CSV : %s", args.output)
    log.info("   Phương thức: %s", "BROWSER (bắt buộc)" if args.browser else "AUTO (Graph API ưu tiên)")
    log.info("=" * 60)

    from src.facebook.page_crawler import PageCrawler

    crawler = PageCrawler(
        page_url=args.page_url,
        max_posts=args.max_posts,
        output_csv=args.output,
        output_json=args.output_json,
        output_summary=args.output_summary,
        since=args.since,
        until=args.until,
        force_browser=args.browser,
    )

    try:
        summary = crawler.run()
        print("\n" + "=" * 60)
        print("✅ Crawl hoàn tất!")
        print(f"   Tổng bài tìm thấy : {summary.total_found}")
        print(f"   Bài mới            : {summary.new_posts}")
        print(f"   Bài trùng          : {summary.duplicate_posts}")
        print(f"   Bài lỗi            : {summary.failed_posts}")
        print(f"   Phương thức        : {summary.crawl_method}")
        print(f"   CSV                : {args.output}")
        print(f"   JSON               : {args.output_json}")
        print(f"   Summary            : {args.output_summary}")
        print("=" * 60)
        sys.exit(0)
    except KeyboardInterrupt:
        log.warning("🛑 Dừng bởi người dùng.")
        sys.exit(130)
    except Exception as exc:
        import traceback
        log.error("❌ Lỗi không xử lý được: %s", exc)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
