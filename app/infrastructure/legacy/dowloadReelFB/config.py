import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}

# Chrome CDP Configuration
CHROME_CDP_URL = os.getenv("CHROME_CDP_URL", "http://localhost:9222")

# Directories and files
OUTPUT_DIR = BASE_DIR / "downloads"
COOKIES_FILE = BASE_DIR / "cookies.txt"
LOG_FILE = BASE_DIR / "downloader.log"               # File log text thô
DOWNLOAD_LOG_JSON = BASE_DIR / "download_log.json"    # File JSON lưu lịch sử download
METADATA_FILE = BASE_DIR / "metadata.json"
CHROME_PROFILE_DIR = BASE_DIR / "chrome_debug_profile" # Thư mục profile Chrome riêng biệt

# Download Configuration
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
VIDEO_QUALITY = "best"  # Giá trị mặc định là "best" để tránh build sai format filter của yt-dlp
MAX_WORKERS = 3

# Cleanup Configuration
CLEANUP_HOURS = 24
JOBS_DATABASE_PATH = Path(
    os.path.expanduser(os.getenv("DATABASE_PATH", str(BASE_DIR.parent / "data" / "jobs.sqlite3")))
).resolve()

# Interaction Configuration
ENABLE_REEL_LIKE = _env_bool("ENABLE_REEL_LIKE", False)
ENABLE_POST_LIKE = ENABLE_REEL_LIKE  # Backward-compatible name used by existing interaction code.
ENABLE_COMMENT_LIKE = _env_bool("ENABLE_COMMENT_LIKE", False)
COMMENT_LIKE_MODE = "TOP_N"  # Options: "TOP_N", "RANDOM", "CONDITION"
COMMENT_LIKE_TOP_N = 5
COMMENT_LIKE_RANDOM_MIN = 10
COMMENT_LIKE_RANDOM_MAX = 15
LIKE_DELAY_MIN = 1.0
LIKE_DELAY_MAX = 3.0
MAX_POST_LIKE_PER_DAY = 100
MAX_COMMENT_LIKE_PER_DAY = 200
SKIP_AUTHOR_COMMENT = True
LIKE_CONDITION_MIN_LENGTH = 10
LIKE_SKIP_LINK = True
LIKE_SKIP_EMOJI_ONLY = True
RANDOM_SKIP_RATE = 0.1  # 10% chance to skip randomly

# Activity Log File
ACTIVITY_LOG_JSON = BASE_DIR / "activity_log.json"
