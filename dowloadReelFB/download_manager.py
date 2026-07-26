import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

import yt_dlp
from rich.console import Console
from rich.progress import (
    Progress, SpinnerColumn, TextColumn,
    BarColumn, TaskProgressColumn, TimeRemainingColumn
)
from rich.table import Table
from rich.panel import Panel

from metadata_manager import MetadataManager
from config import (
    OUTPUT_DIR, COOKIES_FILE, USER_AGENT, VIDEO_QUALITY, MAX_WORKERS, DOWNLOAD_LOG_JSON
)
from logger import log_exception

console = Console()
logger = logging.getLogger("fb_downloader")
SUPPORTED_VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov"})

# ─────────────────────────────────────────────
# DATA CLASS
# ─────────────────────────────────────────────
@dataclass
class VideoInfo:
    url: str
    title: str           = ""
    video_id: str        = ""
    duration: int        = 0          # giây
    file_path: str       = ""
    status: str          = "pending"  # pending | downloading | done | error
    error_msg: str       = ""
    downloaded_at: str   = ""
    file_size_mb: float  = 0.0


def validate_video_file(path: Path) -> Path:
    """Return an absolute validated video path or raise a clear error."""
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"Downloaded video does not exist: {candidate}")
    if not candidate.is_file():
        raise ValueError(f"Downloaded video path is not a regular file: {candidate}")
    if candidate.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        raise ValueError(f"Unsupported downloaded video extension: {candidate.suffix or '<none>'}")
    if candidate.stat().st_size <= 0:
        raise ValueError(f"Downloaded video is empty: {candidate}")
    return candidate


def resolve_downloaded_file(ydl, info: dict, hook_files: List[str]) -> Path:
    """Resolve yt-dlp's actual output, including merged output formats."""
    raw_candidates: list[str] = list(reversed([item for item in hook_files if item]))
    requested = info.get("requested_downloads") or []
    for item in requested:
        if isinstance(item, dict) and item.get("filepath"):
            raw_candidates.append(item["filepath"])
    if info.get("filepath"):
        raw_candidates.append(info["filepath"])
    prepared = Path(ydl.prepare_filename(info))
    raw_candidates.extend(
        str(prepared.with_suffix(extension)) for extension in SUPPORTED_VIDEO_EXTENSIONS
    )
    raw_candidates.append(str(prepared))

    validation_errors: list[str] = []
    seen: set[Path] = set()
    for raw_path in raw_candidates:
        candidate = Path(raw_path).expanduser().resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return validate_video_file(candidate)
        except (FileNotFoundError, ValueError) as exc:
            validation_errors.append(str(exc))
    message = validation_errors[-1] if validation_errors else "yt-dlp did not report an output path"
    raise FileNotFoundError(f"No valid downloaded video output was found: {message}")


# ─────────────────────────────────────────────
# COOKIE CONVERTER HELPER
# ─────────────────────────────────────────────
def check_and_convert_cookies(cookies_path: Path):
    """Kiểm tra và tự động chuyển đổi cookie thô dạng string sang Netscape format nếu cần."""
    if not cookies_path.exists():
        return
    try:
        with open(cookies_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        
        # Nếu content không bắt đầu bằng Netscape header và trông giống chuỗi cookie thô
        if not content.startswith("# HTTP Cookie File") and not content.startswith("# Netscape") and ";" in content and "=" in content:
            logger.info("Phát hiện cookie dạng chuỗi thô. Đang tự động chuyển đổi sang Netscape format...")
            
            # Tách các cặp key-value
            pairs = [p.strip() for p in content.split(";") if "=" in p]
            netscape_lines = [
                "# Netscape HTTP Cookie File",
                "# This file was automatically converted from raw cookie string.",
                ""
            ]
            for pair in pairs:
                parts = pair.split("=", 1)
                name = parts[0].strip()
                val = parts[1].strip()
                # Thêm vào Netscape format
                netscape_lines.append(f".facebook.com\tTRUE\t/\tTRUE\t2000000000\t{name}\t{val}")
            
            # Ghi đè lại file cookies.txt
            with open(cookies_path, "w", encoding="utf-8") as f:
                f.write("\n".join(netscape_lines) + "\n")
            logger.info("Chuyển đổi cookie Netscape thành công!")
    except Exception as e:
        logger.error(f"Lỗi tự động chuyển đổi cookie: {e}")


# ─────────────────────────────────────────────
# HÀM BUILD OPTIONS cho yt-dlp
# ─────────────────────────────────────────────
def build_ydl_opts(output_path: Path, quiet: bool = True) -> dict:
    """Tạo cấu hình yt-dlp tối ưu cho Facebook."""
    
    # Tự động convert cookies nếu là định dạng raw text
    cookies_path = Path(COOKIES_FILE)
    check_and_convert_cookies(cookies_path)
    
    # Format chất lượng
    if VIDEO_QUALITY == "best":
        fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    elif VIDEO_QUALITY == "worst":
        fmt = "worstvideo+worstaudio/worst"
    elif "/" in str(VIDEO_QUALITY) or "[" in str(VIDEO_QUALITY):
        fmt = VIDEO_QUALITY
    else:
        fmt = f"bestvideo[height<={VIDEO_QUALITY}][ext=mp4]+bestaudio[ext=m4a]/best[height<={VIDEO_QUALITY}]/best"

    opts = {
        "format": fmt,
        "outtmpl": str(output_path / "%(upload_date)s_%(id)s_%(title).50s.%(ext)s"),
        "quiet": quiet,
        "no_warnings": quiet,
        "ignoreerrors": True,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "http_headers": {
            "User-Agent": USER_AGENT,
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
            "Referer": "https://www.facebook.com/",
        },
        "socket_timeout": 30,
        "extractor_args": {
            "facebook": {
                "api_version": "v18.0",
            }
        },
    }

    # Thêm cookies nếu có
    if cookies_path.exists():
        opts["cookiefile"] = str(cookies_path)
        console.print(f"[green]✓ Dùng cookies từ file:[/] {cookies_path}")
    else:
        console.print(f"[yellow]⚠ Không tìm thấy {cookies_path}, download không cần đăng nhập[/]")

    return opts


# ─────────────────────────────────────────────
# LẤY THÔNG TIN VIDEO (không download)
# ─────────────────────────────────────────────
def get_video_info(url: str) -> Optional[VideoInfo]:
    """Trích xuất metadata của video mà không download."""
    opts = build_ydl_opts(OUTPUT_DIR, quiet=True)
    opts["skip_download"] = True

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None

            return VideoInfo(
                url=url,
                title=info.get("title", "Unknown"),
                video_id=info.get("id", ""),
                duration=info.get("duration", 0),
            )
    except Exception as e:
        log_exception(logger, f"Lỗi lấy info: {url}", e)
        return VideoInfo(url=url, status="error", error_msg=str(e))


# ─────────────────────────────────────────────
# DOWNLOAD MỘT VIDEO
# ─────────────────────────────────────────────
def download_single(video: VideoInfo, progress=None, task_id=None, metadata_mgr: Optional[MetadataManager] = None) -> VideoInfo:
    """Download một video Facebook, trả về VideoInfo đã cập nhật và ghi vào metadata."""
    if metadata_mgr is None:
        metadata_mgr = MetadataManager()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    video.status = "downloading"
    logger.info("Start downloading...")

    downloaded_files = []

    def progress_hook(d):
        if d["status"] == "downloading" and progress and task_id is not None:
            total   = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            current = d.get("downloaded_bytes", 0)
            if total > 0:
                progress.update(task_id, completed=current, total=total)
        elif d["status"] == "finished":
            downloaded_files.append(d.get("filename", ""))

    opts = build_ydl_opts(OUTPUT_DIR, quiet=True)
    opts["progress_hooks"] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video.url, download=True)
            if not info:
                raise RuntimeError("yt-dlp returned no metadata for the Reel")

            output_file = resolve_downloaded_file(ydl, info, downloaded_files)
            video.file_path = str(output_file)
            video.file_size_mb = round(output_file.stat().st_size / 1024 / 1024, 2)
            video.title = info.get("title", video.title)
            video.video_id = info.get("id", video.video_id)
            video.duration = info.get("duration", 0)
            video.status = "done"
            video.downloaded_at = datetime.now().isoformat()

            logger.info(f"✓ Done: {video.title} ({video.file_size_mb} MB) | URL: {video.url}")
            logger.info("Download completed")
            metadata_mgr.add_record(output_file.name, video.downloaded_at)

    except Exception as e:
        video.status    = "error"
        video.error_msg = str(e)
        log_exception(logger, f"✗ Lỗi download {video.url}", e)

    return video


# ─────────────────────────────────────────────
# DOWNLOAD NHIỀU VIDEO
# ─────────────────────────────────────────────
def download_batch(urls: List[str], max_workers: int = MAX_WORKERS, metadata_mgr: Optional[MetadataManager] = None) -> List[VideoInfo]:
    """Download nhiều video song song."""
    if metadata_mgr is None:
        metadata_mgr = MetadataManager()

    results: List[VideoInfo] = []

    console.print(Panel(
        f"[bold cyan]🎬 CDHA Facebook Reels Downloader[/]\n"
        f"📂 Lưu vào : [yellow]{OUTPUT_DIR.resolve()}[/]\n"
        f"🎯 Số URL  : [green]{len(urls)}[/]\n"
        f"⚡ Luồng   : [blue]{max_workers}[/]",
        title="Bắt đầu Download",
        border_style="cyan"
    ))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
        expand=True,
    ) as progress:

        overall = progress.add_task("[cyan]Tổng tiến độ...", total=len(urls))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}

            for url in urls:
                video = VideoInfo(url=url)
                task_id = progress.add_task(
                    f"[yellow]⏳ {url[:60]}...", total=100
                )
                future = executor.submit(download_single, video, progress, task_id, metadata_mgr)
                futures[future] = (video, task_id)

            for future in as_completed(futures):
                _, task_id = futures[future]
                result = future.result()
                results.append(result)

                if result.status == "done":
                    progress.update(task_id, description=f"[green]✓ {result.title[:50]}", completed=100)
                else:
                    progress.update(task_id, description=f"[red]✗ Lỗi: {result.url[:40]}", completed=100)

                progress.advance(overall)

    return results


# ─────────────────────────────────────────────
# LƯU LOG KẾT QUẢ DẠNG JSON
# ─────────────────────────────────────────────
def save_log(results: List[VideoInfo]):
    """Lưu kết quả download vào file JSON."""
    log_path = Path(DOWNLOAD_LOG_JSON)

    # Đọc log cũ nếu có
    existing = []
    if log_path.exists():
        try:
            if log_path.stat().st_size == 0:
                with open(log_path, "w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
            else:
                with open(log_path, encoding="utf-8") as f:
                    existing = json.load(f)
        except Exception as e:
            log_exception(logger, "Error reading existing log, resetting to empty list", e)
            existing = []

    # Gộp và lưu
    all_results = existing + [asdict(r) for r in results]
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    console.print(f"[green]💾 Đã lưu log vào:[/] {log_path.resolve()}")


# ─────────────────────────────────────────────
# IN BÁO CÁO KẾT QUẢ
# ─────────────────────────────────────────────
def print_report(results: List[VideoInfo]):
    """In bảng tóm tắt kết quả."""
    table = Table(title="📊 Kết Quả Download", border_style="blue", show_lines=True)
    table.add_column("STT", style="dim", width=4)
    table.add_column("Tiêu đề", style="cyan", max_width=40)
    table.add_column("Thời lượng", justify="right")
    table.add_column("Dung lượng", justify="right")
    table.add_column("Trạng thái", justify="center")

    done_count  = 0
    error_count = 0

    for i, r in enumerate(results, 1):
        duration_str = f"{int(r.duration) // 60}:{int(r.duration) % 60:02d}" if r.duration else "N/A"
        size_str     = f"{r.file_size_mb} MB" if r.file_size_mb else "N/A"

        if r.status == "done":
            status_str = "[green]✓ Thành công[/]"
            done_count += 1
        else:
            status_str = f"[red]✗ Lỗi[/]"
            error_count += 1

        table.add_row(str(i), r.title[:40] or r.url[:40], duration_str, size_str, status_str)

    console.print(table)
    console.print(
        f"\n[bold]Tổng kết:[/] "
        f"[green]✓ {done_count} thành công[/]  "
        f"[red]✗ {error_count} lỗi[/]  "
        f"📁 Thư mục: [yellow]{OUTPUT_DIR.resolve()}[/]\n"
    )
