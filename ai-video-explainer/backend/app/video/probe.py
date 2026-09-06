"""Media probing with FFprobe.

Phase 2: after a file is stored, FFprobe inspects it **on disk** (FFprobe
streams the file itself, so Python memory stays flat regardless of video
size) and we store the extracted metadata in SQLite.

Error semantics are distinct on purpose:

- ffprobe binary cannot run at all  -> ``FFmpegUnavailableError`` (503)
- ffprobe ran but rejected the file -> ``InvalidVideoError`` (422)
  (corrupt data, unsupported container, no video stream, impossible
  duration/dimensions)
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app.utils.errors import FFmpegUnavailableError, InvalidVideoError, StorageError
from app.utils.logging import get_logger

logger = get_logger("app.video.probe")


def _parse_rational(value: str | None) -> float | None:
    """'30000/1001' -> 29.970... ; '25/1' -> 25.0 ; None/'0/0' -> None."""
    if not value or value in ("0/0", "0", "N/A"):
        return None
    try:
        num, _, den = value.partition("/")
        num_f, den_f = float(num), float(den or "1")
        if den_f == 0:
            return None
        return num_f / den_f
    except ValueError:
        return None


def _parse_fps(value: str | None) -> float | None:
    parsed = _parse_rational(value)
    return round(parsed, 3) if parsed is not None else None


def _to_float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _container_name(format_name: str | None) -> str | None:
    """'mov,mp4,m4a,3gp,3g2,mj2' -> 'mov' (primary container only)."""
    if not format_name:
        return None
    return format_name.split(",")[0].strip() or None


def probe_media(
    ffprobe_path: str,
    path: str | Path,
    *,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Probe a video file and return validated, normalized metadata.

    Raises:
        StorageError: file missing/unreadable before probing.
        FFmpegUnavailableError: ffprobe could not be executed at all.
        InvalidVideoError: ffprobe rejected the file, or the file does not
            contain a usable video stream (no video, empty/corrupt, no
            duration, no dimensions).
    """
    source = Path(path)
    if not source.is_file():
        raise StorageError(f"File '{source}' does not exist or is unreadable.")
    if source.stat().st_size == 0:
        raise InvalidVideoError("The uploaded file is empty and cannot be a video.")

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
            timeout=timeout_seconds,
            **flags,  # type: ignore[arg-type]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FFmpegUnavailableError(f"FFprobe could not run: {exc}") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[:300] or "unrecognized data"
        raise InvalidVideoError(
            f"FFprobe could not read '{source.name}' as video: {stderr}"
        )

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise InvalidVideoError("FFprobe returned unparseable output.") from exc

    streams = data.get("streams") or []
    if not streams:
        raise InvalidVideoError(
            "The file contains no media streams; it is not a valid video."
        )
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )
    if video_stream is None:
        raise InvalidVideoError(
            "No video stream was found in this file. Audio-only files are "
            "not valid video inputs."
        )

    fmt = data.get("format", {})

    # Duration: prefer container duration, fall back to the video stream.
    duration = _to_float(fmt.get("duration"))
    if duration is None:
        duration = _to_float(video_stream.get("duration"))
    if duration is None or duration <= 0:
        raise InvalidVideoError(
            "Could not determine a valid duration for this video."
        )

    width = _to_int(video_stream.get("width"))
    height = _to_int(video_stream.get("height"))
    if not width or not height or width <= 0 or height <= 0:
        raise InvalidVideoError(
            "The video has no usable dimensions; it cannot be analyzed."
        )

    # FPS: prefer r_frame_rate, fall back to avg_frame_rate. Keep both the
    # normalized number and the raw rational string ("30000/1001").
    raw_fps = video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate")
    fps = _parse_fps(raw_fps)
    if raw_fps in (None, "", "N/A", "0/0"):
        raw_fps = None

    audio_stream = next(
        (s for s in streams if s.get("codec_type") == "audio"), None
    )
    bitrate = _to_int(fmt.get("bit_rate"))
    if bitrate is None and video_stream.get("bit_rate"):
        bitrate = _to_int(video_stream.get("bit_rate"))

    metadata = {
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "fps": fps,
        "raw_fps": raw_fps,
        "video_codec": video_stream.get("codec_name"),
        "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
        "container_format": _container_name(fmt.get("format_name")),
        "bitrate": bitrate,
        "file_size": _to_int(fmt.get("size")),
        "has_video": True,
        "has_audio": audio_stream is not None,
    }
    logger.info("Probed '%s': %s", source.name, metadata)
    return metadata


__all__ = ["probe_media", "_parse_fps"]
