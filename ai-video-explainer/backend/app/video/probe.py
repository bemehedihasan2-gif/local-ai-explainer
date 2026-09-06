"""Media probing with FFprobe (foundation; used from Phase 2 onwards).

Extracts duration / width / height / fps for a video file **on disk**.
FFprobe is streamed by FFmpeg itself, so this never loads the video into
Python memory - exactly what the 8 GB target machine needs.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app.utils.errors import FFmpegUnavailableError, StorageError
from app.utils.logging import get_logger

logger = get_logger("app.video.probe")


def _parse_fps(value: str | None) -> float | None:
    """'30000/1001' -> 29.97 ; '25/1' -> 25.0 ; None/0 -> None."""
    if not value or value in ("0/0", "0"):
        return None
    try:
        num, _, den = value.partition("/")
        num_f, den_f = float(num), float(den or "1")
        if den_f == 0:
            return None
        return round(num_f / den_f, 3)
    except ValueError:
        return None


def probe_media(ffprobe_path: str, path: str | Path) -> dict[str, Any]:
    """Probe a file with ffprobe and return normalized metadata.

    Raises:
        FFmpegUnavailableError: ffprobe is missing or failed to run.
        StorageError: file missing/unreadable or output unparseable.
    """
    source = Path(path)
    if not source.is_file():
        raise StorageError(f"File '{source}' does not exist or is unreadable.")

    flags: dict[str, object] = {}
    if os.name == "nt":  # pragma: no cover - Windows-only nicety
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            [
                ffprobe_path, "-v", "error",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(source),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **flags,  # type: ignore[arg-type]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FFmpegUnavailableError(f"FFprobe could not run: {exc}") from exc

    if proc.returncode != 0:
        raise FFmpegUnavailableError(
            f"FFprobe failed on '{source.name}': {(proc.stderr or '').strip()[:300]}"
        )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise StorageError("FFprobe returned invalid output.") from exc

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"), None
    )
    fmt = data.get("format", {})

    def _safe(field_name: str) -> Any:
        raw = fmt.get(field_name) or (video_stream or {}).get(field_name)
        try:
            return float(raw) if raw not in (None, "N/A") else None
        except (TypeError, ValueError):
            return None

    duration = _safe("duration")
    if duration is None and video_stream:
        duration = _parse_fps(video_stream.get("duration"))

    fps = None
    if video_stream:
        fps = _parse_fps(video_stream.get("r_frame_rate")) or _parse_fps(
            video_stream.get("avg_frame_rate")
        )

    metadata = {
        "duration": round(duration, 3) if duration is not None else None,
        "width": int(video_stream["width"]) if video_stream and video_stream.get("width") else None,
        "height": int(video_stream["height"]) if video_stream and video_stream.get("height") else None,
        "fps": fps,
        "container": fmt.get("format_name"),
        "size_bytes": int(fmt["size"]) if fmt.get("size") else None,
    }
    logger.info("Probed '%s': %s", source.name, metadata)
    return metadata


__all__ = ["probe_media"]
