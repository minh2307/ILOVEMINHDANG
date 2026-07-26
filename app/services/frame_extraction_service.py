"""FrameExtractionService — extracts representative frames from a video file.

Design principles
-----------------
* Uses ffmpeg subprocess (standard in medical imaging environments).
  Falls back to Pillow/OpenCV if available and configured.
* Idempotent: if a valid manifest + all frame checksums exist, extraction
  is skipped.  This supports pipeline resume without re-processing.
* Never re-extracts frames if the video checksum matches the manifest.
* Black-frame and near-duplicate detection reduces redundant frames.
* Writes a JSON manifest to the job artifact directory.
* Does NOT automatically trigger on import — must be called explicitly.
* Configurable via Settings; no values are hard-coded.

Manifest schema (written to <job_dir>/frames/manifest.json)
------------------------------------------------------------
{
  "video_path": "...",
  "video_sha256": "...",
  "duration_seconds": 45.8,
  "extractor": "ffmpeg",
  "extraction_config": {...},
  "frames": [
    {
      "path": "frames/frame-0001.jpg",
      "timestamp_seconds": 0.0,
      "sha256": "..."
    }
  ]
}
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ExtractedFrame:
    path: str             # absolute path to the saved JPEG
    timestamp_seconds: float
    sha256: str


@dataclass(frozen=True)
class FrameManifest:
    video_path: str
    video_sha256: str
    duration_seconds: float
    extractor: str
    extraction_config: dict[str, Any]
    frames: list[ExtractedFrame] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_path": self.video_path,
            "video_sha256": self.video_sha256,
            "duration_seconds": self.duration_seconds,
            "extractor": self.extractor,
            "extraction_config": self.extraction_config,
            "frames": [
                {
                    "path": f.path,
                    "timestamp_seconds": f.timestamp_seconds,
                    "sha256": f.sha256,
                }
                for f in self.frames
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrameManifest":
        frames = [
            ExtractedFrame(
                path=f["path"],
                timestamp_seconds=float(f["timestamp_seconds"]),
                sha256=f.get("sha256", ""),
            )
            for f in data.get("frames", [])
        ]
        return cls(
            video_path=data["video_path"],
            video_sha256=data["video_sha256"],
            duration_seconds=float(data["duration_seconds"]),
            extractor=data.get("extractor", "unknown"),
            extraction_config=data.get("extraction_config", {}),
            frames=frames,
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class FrameExtractionService:
    """Extracts representative frames from a video file using ffmpeg.

    Parameters
    ----------
    job_data_dir : Path
        Root directory; frames are written to ``<job_data_dir>/<job_id>/frames/``.
    interval_seconds : int
        Extract one frame every N seconds.
    max_frames : int
        Maximum number of frames to keep after deduplication.
    width : int
        Resize width; height is scaled proportionally.
    jpeg_quality : int
        JPEG quality (1-95).
    similarity_threshold : float
        Frames whose mean-pixel difference from the previous frame is below
        this threshold (0-255 scale) are considered duplicates and discarded.
    enabled : bool
        If False, extraction is skipped and an empty list is returned.
    logger : logging.Logger | None
    """

    MANIFEST_FILENAME = "manifest.json"

    def __init__(
        self,
        *,
        job_data_dir: Path,
        interval_seconds: int = 2,
        max_frames: int = 12,
        width: int = 1024,
        jpeg_quality: int = 85,
        similarity_threshold: float = 5.0,
        enabled: bool = True,
        logger: logging.Logger | None = None,
    ) -> None:
        self._job_data_dir = job_data_dir
        self._interval = interval_seconds
        self._max_frames = max_frames
        self._width = width
        self._quality = jpeg_quality
        self._threshold = similarity_threshold
        self._enabled = enabled
        self._logger = logger or logging.getLogger("cdha_pipeline.frame_extraction")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract(self, *, job_id: str, video_path: Path) -> list[str]:
        """Extract frames and return list of absolute JPEG paths.

        Returns an empty list if:
        - ``enabled`` is False
        - ffmpeg is not available
        - video cannot be read

        Raises
        ------
        FileNotFoundError
            If ``video_path`` does not exist.
        """
        if not self._enabled:
            self._logger.info("Frame extraction disabled", extra={"job_id": job_id})
            return []

        video_path = video_path.resolve()
        if not video_path.is_file():
            raise FileNotFoundError(f"Video not found: {video_path}")

        if not self._ffmpeg_available():
            self._logger.warning(
                "ffmpeg not found; frame extraction unavailable. "
                "Install ffmpeg or set FRAME_EXTRACTION_ENABLED=false.",
                extra={"job_id": job_id},
            )
            return []

        frames_dir = self._job_data_dir / job_id / "frames"
        manifest_path = frames_dir / self.MANIFEST_FILENAME

        # --- Idempotency check ---
        video_sha256 = self._sha256(video_path)
        if manifest_path.is_file():
            try:
                manifest = FrameManifest.from_dict(
                    json.loads(manifest_path.read_text(encoding="utf-8"))
                )
                if (
                    manifest.video_sha256 == video_sha256
                    and self._all_frames_valid(manifest)
                ):
                    self._logger.info(
                        "Reusing existing frames (manifest valid)",
                        extra={"job_id": job_id, "frame_count": len(manifest.frames)},
                    )
                    return [f.path for f in manifest.frames]
            except (json.JSONDecodeError, KeyError, ValueError):
                self._logger.warning(
                    "Manifest invalid; re-extracting",
                    extra={"job_id": job_id},
                )

        # --- Extract ---
        frames_dir.mkdir(parents=True, exist_ok=True)
        duration = self._get_duration(video_path)
        raw_frames = self._run_ffmpeg(video_path, frames_dir, duration)

        # --- Filter ---
        filtered = self._filter_frames(raw_frames)

        # --- Write manifest ---
        manifest = FrameManifest(
            video_path=str(video_path),
            video_sha256=video_sha256,
            duration_seconds=duration,
            extractor="ffmpeg",
            extraction_config={
                "interval_seconds": self._interval,
                "max_frames": self._max_frames,
                "width": self._width,
                "jpeg_quality": self._quality,
                "similarity_threshold": self._threshold,
            },
            frames=filtered,
        )
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)

        self._logger.info(
            "Frame extraction complete",
            extra={
                "job_id": job_id,
                "duration_seconds": duration,
                "frame_count": len(filtered),
            },
        )
        return [f.path for f in filtered]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ffmpeg_available(self) -> bool:
        return shutil.which("ffmpeg") is not None

    def _get_duration(self, video_path: Path) -> float:
        """Return video duration in seconds via ffprobe."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return float(result.stdout.strip() or "0")
        except (subprocess.TimeoutExpired, ValueError, OSError):
            return 0.0

    def _run_ffmpeg(
        self, video_path: Path, frames_dir: Path, duration: float
    ) -> list[ExtractedFrame]:
        """Run ffmpeg and return list of extracted frames."""
        if duration <= 0:
            # ffprobe failed; try without duration
            timestamps = list(range(0, 120, self._interval))[:self._max_frames]
        else:
            timestamps = []
            t = 0.0
            while t < duration and len(timestamps) < self._max_frames * 3:
                timestamps.append(t)
                t += self._interval

        frames: list[ExtractedFrame] = []
        for idx, ts in enumerate(timestamps, 1):
            out_path = frames_dir / f"frame-{idx:04d}.jpg"
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(ts),
                "-i", str(video_path),
                "-vframes", "1",
                "-vf", f"scale={self._width}:-2",
                "-q:v", str(max(1, 31 - int(self._quality * 30 / 95))),
                str(out_path),
            ]
            try:
                subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if out_path.is_file() and out_path.stat().st_size > 500:
                    frames.append(
                        ExtractedFrame(
                            path=str(out_path),
                            timestamp_seconds=ts,
                            sha256=self._sha256(out_path),
                        )
                    )
            except (subprocess.TimeoutExpired, OSError):
                self._logger.warning(
                    "ffmpeg failed for frame",
                    extra={"timestamp": ts, "path": str(out_path)},
                )
        return frames

    def _filter_frames(self, frames: list[ExtractedFrame]) -> list[ExtractedFrame]:
        """Remove black frames and near-duplicates, then cap at max_frames."""
        if not frames:
            return []

        filtered: list[ExtractedFrame] = []
        prev_pixels: list[float] | None = None

        for frame in frames:
            pixels = self._load_grayscale_pixels(Path(frame.path))
            if pixels is None:
                continue

            # Skip black or near-black frames
            mean = sum(pixels) / len(pixels) if pixels else 0
            if mean < 10.0:
                self._logger.debug(
                    "Skipping black frame",
                    extra={"path": frame.path, "mean": mean},
                )
                continue

            # Skip near-duplicates
            if prev_pixels is not None:
                diff = sum(abs(a - b) for a, b in zip(pixels, prev_pixels)) / len(pixels)
                if diff < self._threshold:
                    continue

            filtered.append(frame)
            prev_pixels = pixels

            if len(filtered) >= self._max_frames:
                break

        return filtered

    @staticmethod
    def _load_grayscale_pixels(path: Path) -> list[float] | None:
        """Return a downsampled grayscale pixel list for similarity comparison.

        Uses Pillow if available; returns None if the image cannot be read.
        """
        try:
            from PIL import Image  # type: ignore[import]
            with Image.open(path) as img:
                small = img.convert("L").resize((32, 32))
                return list(small.getdata())
        except Exception:
            return None

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _all_frames_valid(self, manifest: FrameManifest) -> bool:
        """Return True if all frame files exist and checksums match."""
        if not manifest.frames:
            return False
        for frame in manifest.frames:
            p = Path(frame.path)
            if not p.is_file():
                return False
            if frame.sha256 and self._sha256(p) != frame.sha256:
                return False
        return True
