from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.config.settings import Settings
from app.services.privacy_service import PrivacyService


FACEBOOK_SCREENSHOT_ORDER = (
    "01-detailed-analysis.png",
    "02-final-result.png",
)


class PostContentValidationError(ValueError):
    pass


class PostContentService:
    def __init__(self, settings: Settings, privacy: PrivacyService | None = None) -> None:
        self.settings = settings
        self.privacy = privacy or PrivacyService()

    @staticmethod
    def normalize_target_url(value: str) -> str:
        raw = str(value or "").strip()
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in {
            "facebook.com", "www.facebook.com", "m.facebook.com"
        }:
            raise PostContentValidationError("FACEBOOK_TARGET_URL must be a valid HTTPS Facebook URL")
        path = re.sub(r"/{2,}", "/", parsed.path)
        if path.rstrip("/").casefold() in {"", "/login", "/checkpoint"}:
            raise PostContentValidationError(
                "FACEBOOK_TARGET_URL must identify a Page, profile, group, Reel, or post"
            )
        path = path.rstrip("/") or "/"
        return urlunsplit(("https", "www.facebook.com", path, "", ""))

    def build_post(
        self,
        *,
        source_url: str,
        key_findings: list[str],
        impression: str | None,
        clinical_factors: str = "",
        operator_text: str | None = None,
        cdha_view_url: str = "",
    ) -> str:
        if operator_text is not None:
            text = operator_text.strip()
        else:
            findings = [str(item).strip() for item in key_findings if str(item).strip()]
            if not findings:
                raise PostContentValidationError(
                    "CDHA Key Findings are missing; manual post editing is required"
                )
            if not str(impression or "").strip():
                raise PostContentValidationError(
                    "CDHA Impression is missing; manual post editing is required"
                )
            bullets = "\n".join(f"• {item}" for item in findings)
            text = f"""📌 CA LÂM SÀNG SIÊU ÂM

Video được phân tích bằng công cụ hỗ trợ chẩn đoán hình ảnh CDHA.AI.

🔍 Ghi nhận chính:
{bullets}

📝 Nhận định:
{str(impression).strip()}

⚠️ Nội dung được sử dụng cho mục đích tham khảo, chia sẻ và trao đổi
chuyên môn. Kết quả không thay thế việc thăm khám hoặc chẩn đoán trực tiếp
của bác sĩ có chuyên môn.

Nguồn video:
{source_url.strip()}

Nguồn phân tích:
{cdha_view_url}&ref=CD2ED52966

#CDHA #SieuAm #ChanDoanHinhAnh #MedicalAI #HoiChan"""
        self.validate_post_text(text, source_url=source_url, cdha_view_url=cdha_view_url)
        return text

    def validate_post_text(self, text: str, *, source_url: str = "", cdha_view_url: str = "") -> None:
        value = str(text or "").strip()
        if not value:
            raise PostContentValidationError("Facebook post content cannot be empty")
        privacy_input = value.replace(source_url, "") if source_url else value
        if cdha_view_url:
            privacy_input = privacy_input.replace(cdha_view_url, "")
        if self.privacy.contains_obvious_identifier(privacy_input):
            raise PostContentValidationError("Facebook post content contains identifying information")
        if re.search(r"(?:^|\s)(?:/home/|/media/|/tmp/|[A-Za-z]:\\)", value):
            raise PostContentValidationError("Facebook post content contains a local file path")
        if re.search(r"(?i)\b(?:password|authorization|bearer|access[_ -]?token)\s*[:=]", value):
            raise PostContentValidationError("Facebook post content contains credential-like data")

    def select_screenshots(
        self, job_id: str, selected_names: list[str] | None = None
    ) -> tuple[list[Path], list[str]]:
        folder = (self.settings.job_data_dir / job_id / "screenshots").resolve()
        ordered_names = list(FACEBOOK_SCREENSHOT_ORDER)
        if selected_names is not None:
            selected = set(selected_names)
            unknown = selected.difference(ordered_names)
            if unknown:
                raise PostContentValidationError(
                    "Unknown screenshot selection: " + ", ".join(sorted(unknown))
                )
            ordered_names = [name for name in ordered_names if name in selected]
        warnings: list[str] = []
        images: list[Path] = []
        for name in ordered_names:
            path = folder / name
            if not path.exists():
                warnings.append(f"Optional screenshot is missing: {name}")
                continue
            images.append(self.validate_image(path))
        if not images:
            raise PostContentValidationError("No valid Phase 3 screenshots are available")
        if len(images) > self.settings.facebook_max_image_count:
            raise PostContentValidationError(
                f"Screenshot count exceeds limit of {self.settings.facebook_max_image_count}"
            )
        return images, warnings

    def validate_image(self, image_path: Path) -> Path:
        path = Path(image_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise PostContentValidationError(f"Screenshot does not exist: {path}")
        if path.stat().st_size <= 0:
            raise PostContentValidationError(f"Screenshot is empty: {path.name}")
        if path.suffix.lower() not in self.settings.facebook_allowed_image_extensions:
            raise PostContentValidationError(f"Unsupported screenshot extension: {path.suffix}")
        limit = self.settings.facebook_max_image_size_mb * 1024 * 1024
        if path.stat().st_size > limit:
            raise PostContentValidationError(
                f"Screenshot exceeds {self.settings.facebook_max_image_size_mb} MB: {path.name}"
            )
        try:
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
        except ImportError as exc:
            raise PostContentValidationError("Pillow is required to validate screenshots") from exc
        except Exception as exc:
            raise PostContentValidationError(f"Screenshot cannot be opened: {path.name}") from exc
        return path

    @staticmethod
    def image_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def content_fingerprint(self, target_url: str, post_text: str, images: list[Path]) -> str:
        payload = "\n".join(
            [
                self.normalize_target_url(target_url),
                "\n".join(line.rstrip() for line in post_text.strip().splitlines()),
                *[self.image_sha256(path) for path in images],
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def build_permalink_comment(post_url: str) -> str:
        return f"Chi tiết: {post_url.strip()}"

    @staticmethod
    def write_text_atomic(path: Path, text: str) -> Path:
        destination = Path(path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(destination)
        return destination
