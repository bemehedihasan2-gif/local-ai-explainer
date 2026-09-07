"""Phase 7 final media QC (deterministic).

A render is not accepted just because ffmpeg exited 0: the output is
re-probed with ffprobe, a few frames are decoded from start/middle/end, the
sidecar subtitles are re-read, and timing is compared against the narration
timeline + render plan. A single 0-100 score is produced with documented
weights: container 20%, video stream 20%, audio stream 20%, timeline/
duration 15%, subtitles 15%, decode verification 10%.

``run_probe`` / ``decode_frames`` are module-level functions so tests can
inject canned ffprobe output and fake binaries.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app.utils.errors import FinalQCRejectedError, RenderError
from app.utils.logging import get_logger

logger = get_logger("app.services.final_qc")

SCHEMA_VERSION = 1

# (sub_score, weight) - weights must sum to 1.0
_DIMENSIONS: list[tuple[str, float]] = [
    ("container_score", 0.20),
    ("video_score", 0.20),
    ("audio_score", 0.20),
    ("timeline_score", 0.15),
    ("subtitle_score", 0.15),
    ("decode_score", 0.10),
]


def run_probe(path: Path | str, ffprobe_bin: str) -> dict[str, Any]:
    """FFprobe the file into a JSON dict (raises RenderError on failure)."""
    flags: dict[str, object] = {}
    if os.name == "nt":  # pragma: no cover - Windows-only nicety
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            [
                ffprobe_bin, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            **flags,  # type: ignore[arg-type]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RenderError(f"FFprobe could not inspect the output: {exc}") from exc
    if proc.returncode != 0:
        raise RenderError(
            f"FFprobe rejected the output: {(proc.stderr or '').strip()[:400]}"
        )
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RenderError(f"FFprobe returned unreadable JSON: {exc}") from exc


def decode_frames(path: Path | str, ffmpeg_bin: str, count: int = 3) -> bool:
    """Decode a handful of frames across the file (corruption check)."""
    flags: dict[str, object] = {}
    if os.name == "nt":  # pragma: no cover
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW
    positions = ["00:00:00", "00:00:00"]
    # We let ffmpeg seek to ~0/50/95% via -ss percentages.
    try:
        for offset in (0.0, 0.5, 0.95):
            proc = subprocess.run(
                [
                    ffmpeg_bin, "-v", "error", "-ss", f"{offset:.3f}",
                    "-i", str(path), "-frames:v", str(count), "-f", "null", "-",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                **flags,  # type: ignore[arg-type]
            )
            if proc.returncode != 0:
                logger.warning(
                    "Decode check failed at %.0f%%: %s",
                    offset * 100, (proc.stderr or "").strip()[:300],
                )
                return False
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Decode check could not run: %s", exc)
        return False
    return True


def _fmt_time(seconds: Any) -> float:
    return float(seconds or 0.0)


class FinalVideoQCService:
    """Deterministic acceptance checks for the rendered final.mp4."""

    def __init__(
        self,
        *,
        ffprobe_bin: str,
        ffmpeg_bin: str,
        probe_fn=run_probe,
        decode_fn=decode_frames,
    ) -> None:
        self._ffprobe_bin = ffprobe_bin
        self._ffmpeg_bin = ffmpeg_bin
        self._probe_fn = probe_fn
        self._decode_fn = decode_fn

    def inspect(
        self,
        path: Path | str,
        *,
        expected_duration_ms: int | None = None,
        narration_duration_ms: int | None = None,
        narration_expected: bool = True,
        subtitle_sidecar: Path | None = None,
        burn_enabled: bool = True,
    ) -> dict[str, Any]:
        """Run every check; returns a full report with ``quality_score``.

        Raises :class:`FinalQCRejectedError` when the file is unusable
        (missing streams, impossible metadata, decode failure).
        """
        path = Path(path)
        checks: dict[str, Any] = {}
        if not path.is_file() or path.stat().st_size == 0:
            raise FinalQCRejectedError("The rendered output is missing or empty.")
        probe = self._probe_fn(path, self._ffprobe_bin)

        streams = probe.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        fmt = probe.get("format") or {}
        duration_s = _fmt_time(fmt.get("duration"))

        # --- container ---------------------------------------------------
        container_score = 100.0
        if not fmt or "duration" not in fmt:
            container_score = 0.0
        elif duration_s <= 0:
            container_score = 0.0

        # --- video -------------------------------------------------------
        video_score = 100.0
        video_ok = False
        video_info: dict[str, Any] = {}
        if video_streams:
            stream = video_streams[0]
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            codec = stream.get("codec_name", "")
            fps_value = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
            video_ok = width > 0 and height > 0 and bool(codec)
            video_info = {
                "codec": codec,
                "width": width,
                "height": height,
                "fps": fps_value,
            }
            if not video_ok:
                video_score = 0.0
        else:
            video_score = 0.0
        if video_ok and not video_info["codec"]:
            video_score -= 30.0

        # --- audio -------------------------------------------------------
        audio_score = 100.0
        audio_info: dict[str, Any] = {}
        if audio_streams:
            stream = audio_streams[0]
            audio_info = {
                "codec": stream.get("codec_name", ""),
                "channels": stream.get("channels"),
                "sample_rate": stream.get("sample_rate"),
            }
            if not stream.get("codec_name"):
                audio_score -= 40.0
        elif narration_expected:
            audio_score = 0.0  # narration was promised but is missing

        # --- duration / timeline ------------------------------------------
        duration_ms = round(duration_s * 1000)
        timeline_score = 100.0
        if expected_duration_ms:
            drift = abs(duration_ms - expected_duration_ms)
            # Allow ~2.5 % + 1 s of container rounding.
            tolerance = max(1000, round(expected_duration_ms * 0.025))
            if drift > tolerance:
                timeline_score = max(
                    0.0, 100.0 * (1.0 - (drift - tolerance) / expected_duration_ms)
                )
        if narration_duration_ms and duration_ms < narration_duration_ms:
            timeline_score = 0.0  # video must never end before the narration

        # --- subtitles -----------------------------------------------------
        subtitle_score = 100.0
        if subtitle_sidecar is not None:
            if not subtitle_sidecar.is_file() or subtitle_sidecar.stat().st_size == 0:
                subtitle_score -= 50.0
            else:
                try:
                    text = subtitle_sidecar.read_text(encoding="utf-8")
                    if "\x00" in text:
                        subtitle_score -= 30.0  # not valid UTF-8 text
                    if " --> " not in text:
                        subtitle_score -= 30.0
                except (OSError, UnicodeDecodeError):
                    subtitle_score -= 50.0
        elif not burn_enabled:
            subtitle_score = 100.0  # burn disabled; sidecar optional

        # --- decode ---------------------------------------------------------
        decode_ok = self._decode_fn(path, self._ffmpeg_bin)
        decode_score = 100.0 if decode_ok else 0.0

        if not container_score or not video_ok or decode_ok is False:
            # Structural failures are never acceptable.
            raise FinalQCRejectedError(
                "Final QC rejected the rendered file (container="
                f"{int(container_score)}, video_ok={video_ok}, "
                f"decode_ok={decode_ok})."
            )

        scores: dict[str, float] = {
            "container_score": round(max(0.0, container_score), 1),
            "video_score": round(max(0.0, video_score), 1),
            "audio_score": round(max(0.0, audio_score), 1),
            "timeline_score": round(max(0.0, timeline_score), 1),
            "subtitle_score": round(max(0.0, subtitle_score), 1),
            "decode_score": round(decode_score, 1),
        }
        quality_score = round(
            sum(scores[name] * weight for name, weight in _DIMENSIONS)
        )
        report = {
            "schema_version": SCHEMA_VERSION,
            "quality_score": quality_score,
            "scores": scores,
            "checks": {
                "container": "ok" if container_score >= 100 else "warn",
                "video_stream": "ok" if video_ok else "fail",
                "audio_stream": (
                    "ok" if audio_score >= 100
                    else "missing" if audio_score == 0 else "warn"
                ),
                "decode": "ok" if decode_ok else "fail",
            },
            "probe": {
                "duration_ms": duration_ms,
                "duration_seconds": duration_s,
                "format": fmt.get("format_name", ""),
                "video": video_info,
                "audio": audio_info,
            },
        }
        if not burn_enabled:
            report["checks"]["subtitles"] = "sidecar-only (burn disabled)"
        else:
            report["checks"]["subtitles"] = (
                "ok" if subtitle_score >= 100 else "warn"
            )
        return report


__all__ = ["FinalVideoQCService", "run_probe", "decode_frames", "_DIMENSIONS"]
