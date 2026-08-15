from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.config.settings import Settings
from app.domain.models.cdha_clinical_summary import (
    CDHAClinicalSummary,
    ClinicalSummaryValidationError,
)
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
        summary = self.validate_clinical_summary(
            key_findings=key_findings,
            impression=impression,
            cdha_view_url=cdha_view_url,
        )
        if operator_text is not None:
            text = operator_text.strip()
        else:
            findings = [self._normalize_measurement_display(item) for item in summary.key_findings]
            display_impression = self._normalize_measurement_display(summary.impression)
            bullets = "\n".join(f"• {item}" for item in findings)
            hashtags = " ".join(self.settings.facebook_post_hashtags)
            text = f"""📌 CA LÂM SÀNG SIÊU ÂM

Video được phân tích bằng công cụ hỗ trợ chẩn đoán hình ảnh CDHA.AI.

🔍 Ghi nhận chính:
{bullets}

📝 Nhận định:
{display_impression}

⚠️ Nội dung được sử dụng cho mục đích tham khảo, chia sẻ và trao đổi chuyên môn.
Kết quả không thay thế việc thăm khám hoặc chẩn đoán trực tiếp của bác sĩ có chuyên môn.

Nguồn video:
{source_url.strip()}

Nguồn phân tích:
{summary.analysis_url}&ref=CD2ED52966

{hashtags}"""
        self.validate_publish_ready(
            text,
            source_url=source_url,
            cdha_view_url=summary.analysis_url,
            key_findings=summary.key_findings,
            impression=summary.impression,
        )
        return text

    def validate_publish_ready(
        self,
        text: str,
        *,
        source_url: str,
        cdha_view_url: str,
        key_findings: list[str],
        impression: str | None,
    ) -> CDHAClinicalSummary:
        self.validate_post_text(text, source_url=source_url, cdha_view_url=cdha_view_url)
        summary = self.validate_clinical_summary(
            key_findings=key_findings,
            impression=impression,
            cdha_view_url=cdha_view_url,
        )
        parsed_source = urlsplit(str(source_url or "").strip())
        if (
            parsed_source.scheme != "https"
            or (parsed_source.hostname or "").casefold()
            not in {"facebook.com", "www.facebook.com", "m.facebook.com"}
        ):
            raise PostContentValidationError("An exact HTTPS Facebook source URL is required")
        value = str(text or "").strip()
        required_fragments = (
            "📌 CA LÂM SÀNG SIÊU ÂM",
            "🔍 Ghi nhận chính:",
            "📝 Nhận định:",
            "⚠️ Nội dung được sử dụng cho mục đích tham khảo, chia sẻ và trao đổi chuyên môn.",
            "Kết quả không thay thế việc thăm khám hoặc chẩn đoán trực tiếp của bác sĩ có chuyên môn.",
            "Nguồn video:",
            "Nguồn phân tích:",
            source_url.strip(),
            summary.analysis_url,
        )
        missing = [fragment for fragment in required_fragments if not fragment or fragment not in value]
        if missing:
            raise PostContentValidationError(
                "Facebook post is missing required clinical sections or exact source URLs"
            )
        findings_block = self._section(
            value, "🔍 Ghi nhận chính:", "📝 Nhận định:"
        )
        impression_block = self._section(
            value, "📝 Nhận định:", "⚠️ Nội dung"
        )
        if re.search(r"(?im)^\s*[•*-]?\s*(?:key\s*findings?|impression)\s*:?\s*$", findings_block):
            raise PostContentValidationError("CDHA Key Findings contain a field label instead of content")
        if re.search(r"(?im)^\s*(?:key\s*findings?|impression)\s*:?\s*$", impression_block):
            raise PostContentValidationError("CDHA Impression contains a field label instead of content")
        for finding in summary.key_findings:
            if self._normalize_measurement_display(finding) not in findings_block:
                raise PostContentValidationError(
                    "Facebook findings do not match the validated CDHA result"
                )
        if self._normalize_measurement_display(summary.impression) not in impression_block:
            raise PostContentValidationError(
                "Facebook impression does not match the validated CDHA result"
            )
        return summary

    def validate_clinical_summary(
        self,
        *,
        key_findings: list[str],
        impression: str | None,
        cdha_view_url: str,
    ) -> CDHAClinicalSummary:
        try:
            summary = CDHAClinicalSummary.from_values(
                key_findings=key_findings,
                impression=impression,
                analysis_url=cdha_view_url,
            )
        except ClinicalSummaryValidationError as exc:
            raise PostContentValidationError(str(exc)) from exc
        clinical_text = "\n".join((*summary.key_findings, summary.impression))
        if self.privacy.contains_obvious_identifier(clinical_text):
            raise PostContentValidationError("CDHA clinical summary contains identifying information")
        if re.search(r"(?i)\b(?:chắc chắn|khẳng định|100\s*%)\b", clinical_text):
            raise PostContentValidationError("CDHA clinical summary contains an absolute claim")
        if not re.search(
            r"[ăâđêôơưáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]",
            clinical_text,
            re.IGNORECASE,
        ):
            raise PostContentValidationError("CDHA clinical summary must be written in Vietnamese")
        return summary

    def validate_post_text(self, text: str, *, source_url: str = "", cdha_view_url: str = "") -> None:
        value = str(text or "").strip()
        if not value:
            raise PostContentValidationError("Facebook post content cannot be empty")
        # --- Placeholder and template variable gate ---
        if re.search(r"(?i)(?:^|[\s:])(?:n/a|null|undefined)(?:\s|$)", value):
            raise PostContentValidationError(
                "Facebook post content contains a null or N/A placeholder value"
            )
        if re.search(r"\{\{[^}]+\}\}", value):
            raise PostContentValidationError(
                "Facebook post content contains an unfilled template variable ({{...}})"
            )
        if re.search(r"(?i)\[(?:PLACEHOLDER|TODO|FILL[_ -]?IN|INSERT[_ -]?HERE|YOUR[_ -]?TEXT)\]", value):
            raise PostContentValidationError(
                "Facebook post content contains an unfilled placeholder tag"
            )
        if re.search(r"(?im)^\s*(?:ghi nhận chính|nhận định|key findings|impression)\s*:\s*$", value):
            raise PostContentValidationError(
                "Facebook post content contains an empty clinical field label"
            )
        # --- Privacy and credential gate ---
        privacy_input = value.replace(source_url, "") if source_url else value
        if cdha_view_url:
            privacy_input = privacy_input.replace(cdha_view_url, "")
        if self.privacy.contains_obvious_identifier(privacy_input):
            raise PostContentValidationError("Facebook post content contains identifying information")
        if re.search(r"(?:^|\s)(?:/home/|/media/|/tmp/|[A-Za-z]:\\)", value):
            raise PostContentValidationError("Facebook post content contains a local file path")
        if re.search(r"(?i)\b(?:password|authorization|bearer|access[_ -]?token)\s*[:=]", value):
            raise PostContentValidationError("Facebook post content contains credential-like data")

    @staticmethod
    def _section(value: str, start: str, end: str) -> str:
        try:
            return value.split(start, 1)[1].split(end, 1)[0].strip()
        except IndexError as exc:
            raise PostContentValidationError(
                f"Facebook post section is missing: {start}"
            ) from exc

    @staticmethod
    def _normalize_measurement_display(value: str) -> str:
        return re.sub(
            r"(?<=\d)\.(?=\d+\s*(?:mm|cm|m|ml|mL|l|%)(?:\b|\s|[.,;)]|$))",
            ",",
            str(value or "").strip(),
            flags=re.IGNORECASE,
        )

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

    def content_fingerprint(
        self, target_url: str, post_text: str, images: list[Path],
        job_id: str, source_url: str, cdha_view_url: str
    ) -> str:
        # Extract external ID from cdha_view_url if present
        external_id = ""
        if cdha_view_url:
            match = re.search(r"/(\d+)$", cdha_view_url.split("?")[0])
            if match:
                external_id = match.group(1)
        
        payload = "\n".join(
            [
                job_id,
                self.normalize_target_url(target_url),
                source_url.strip(),
                "\n".join(line.rstrip() for line in post_text.strip().splitlines()),
                *[self.image_sha256(path) for path in images],
                external_id,
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
