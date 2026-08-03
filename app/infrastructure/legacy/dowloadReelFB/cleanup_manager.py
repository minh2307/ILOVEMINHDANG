import os
import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from metadata_manager import MetadataManager
from config import JOBS_DATABASE_PATH

logger = logging.getLogger("fb_downloader")

class CleanupManager:
    """Quản lý việc tự động xóa các file video đã tải quá 24 giờ."""

    VALID_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}
    ACTIVE_STATUSES = {
        "CREATED", "DOWNLOADREEL_RUNNING", "DOWNLOADED", "GEMINI_OPENING",
        "NEEDS_GEMINI_LOGIN", "GEMINI_GENERATING", "CLINICAL_FACTORS_GENERATED",
        "CDHA_OPENING", "NEEDS_CDHA_LOGIN", "CDHA_UPLOADING", "CDHA_ANALYZING",
        "CDHA_ANALYZED", "SCREENSHOTS_CAPTURING", "SCREENSHOTS_CAPTURED",
        "WAITING_FOR_REVIEW", "APPROVED", "FACEBOOK_PREPARING",
        "FACEBOOK_WAITING_FOR_MANUAL_REVIEW", "FACEBOOK_PUBLISHING",
        "FACEBOOK_PUBLISHED", "POST_URL_EXTRACTING", "POST_URL_EXTRACTED",
        "COMMENT_ADDING", "COMMENT_ADDED", "RETRY_PENDING",
    }

    @classmethod
    def protected_asset_paths(cls, database_path: Path = JOBS_DATABASE_PATH) -> set[Path]:
        database = Path(database_path).expanduser().resolve()
        if not database.is_file():
            return set()
        protected: set[Path] = set()
        try:
            with sqlite3.connect(database) as connection:
                rows = connection.execute("SELECT status, data_json FROM jobs").fetchall()
            for status, raw_data in rows:
                try:
                    data = json.loads(raw_data or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                if status not in cls.ACTIVE_STATUSES and not data.get("retain_assets"):
                    continue
                for key in ("video_path", "metadata_path"):
                    if data.get(key):
                        protected.add(Path(data[key]).expanduser().resolve())
        except (OSError, sqlite3.Error) as exc:
            logger.warning(f"Cannot inspect active workflow assets; cleanup will be skipped: {exc}")
            return {Path("/").resolve()}
        return protected

    @staticmethod
    def is_cleanup_eligible(file_path: Path, *, is_expired: bool, protected: set[Path]) -> bool:
        return (
            is_expired
            and Path("/").resolve() not in protected
            and Path(file_path).resolve() not in protected
        )

    @classmethod
    def cleanup(
        cls,
        download_dir: Path,
        metadata_mgr: MetadataManager,
        max_age_hours: int = 24,
        jobs_database_path: Path = JOBS_DATABASE_PATH,
    ) -> None:
        """
        Quét thư mục download và xóa các file video đã tồn tại hơn 24 giờ.
        Chỉ xóa các file video (mp4, mkv, webm) và xóa record tương ứng trong metadata.
        """
        if not download_dir.exists():
            logger.warning(f"Download directory {download_dir} does not exist. Skipping cleanup.")
            return

        logger.info("Scanning download directory for expired reels...")
        now = datetime.now()
        metadata_list = metadata_mgr.load()
        metadata_dict = {item["filename"]: item["download_time"] for item in metadata_list}
        protected_paths = cls.protected_asset_paths(jobs_database_path)
        
        # Danh sách các file cần xóa khỏi metadata
        files_to_remove_from_metadata = []
        
        # Duyệt qua các file trong thư mục download
        try:
            for file_path in download_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in cls.VALID_EXTENSIONS:
                    filename = file_path.name
                    is_expired = False
                    
                    # Nếu có trong metadata, kiểm tra download_time
                    if filename in metadata_dict:
                        try:
                            download_time = datetime.fromisoformat(metadata_dict[filename])
                            if now - download_time > timedelta(hours=max_age_hours):
                                is_expired = True
                        except ValueError:
                            # Nếu parse thất bại, dùng mtime của file
                            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                            if now - mtime > timedelta(hours=max_age_hours):
                                is_expired = True
                    else:
                        # Nếu không có trong metadata, dùng mtime của file
                        mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                        if now - mtime > timedelta(hours=max_age_hours):
                            is_expired = True

                    if cls.is_cleanup_eligible(
                        file_path, is_expired=is_expired, protected=protected_paths
                    ):
                        try:
                            file_path.unlink()
                            logger.info(f"Delete expired reel: {filename}")
                            sidecar_path = file_path.with_suffix(".json")
                            if (
                                sidecar_path.is_file()
                                and sidecar_path.resolve() not in protected_paths
                            ):
                                sidecar_path.unlink()
                                logger.info(f"Delete expired reel metadata: {sidecar_path.name}")
                            files_to_remove_from_metadata.append(filename)
                        except Exception as e:
                            logger.error(f"Cannot delete expired file {filename}: {e}")
        except Exception as e:
            logger.error(f"Error during directory scanning: {e}")

        # Đồng thời dọn dẹp các record mồ côi trong metadata (file vật lý không tồn tại)
        for item in metadata_list:
            filename = item["filename"]
            phys_file = download_dir / filename
            if not phys_file.exists() and filename not in files_to_remove_from_metadata:
                files_to_remove_from_metadata.append(filename)

        # Xóa record trong metadata
        for filename in files_to_remove_from_metadata:
            metadata_mgr.remove_record(filename)
