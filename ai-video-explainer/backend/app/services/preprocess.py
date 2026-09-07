"""Phase 3: preprocessing & analysis-asset generation.

Turns a validated ``READY`` project into a ``PREPARED`` project by producing
three files **on disk** with FFmpeg (never in RAM):

- ``analysis/analysis.mp4``  - low-res, constant-fps H.264 copy that later
  vision/OCR/scene stages will read (CPU-friendly, tiny compared to the
  original). No audio - the WAV below carries speech.
- ``thumbnails/poster.jpg`` - a single poster frame for the UI.
- ``audio/audio.wav``       - 16 kHz mono PCM, the universal input for
  local speech-to-text. Skipped for videos without an audio track.

Design rules for the 8 GB target machine:

- FFmpeg streams everything itself; Python memory stays flat.
- Subprocesses are always non-shell argument arrays, with a hard timeout
  per step and ``-v error`` so stderr stays small.
- ``-progress pipe:1`` gives honest, duration-weighted progress; we never
  invent percentages.
- Any failure removes the partial asset files so an interrupted run cannot
  leave corrupt files behind (the caller restores the project to READY).

Output dimensions are computed deterministically from the source metadata
(the scale/fps filters always produce exactly these values), so no second
ffprobe pass over the analysis copy is needed.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services.storage import StorageService
from app.utils.errors import PreprocessError
from app.utils.logging import get_logger

logger = get_logger("app.services.preprocess")

#: Progress weight windows (percent of the whole preprocess job).
_W_ANALYSIS = (0.0, 60.0)   # heaviest step: transcode the analysis copy
_W_THUMB = (60.0, 75.0)
_W_AUDIO = (75.0, 95.0)
_W_DONE = 100.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _parse_out_time(line: str) -> float | None:
    """Return elapsed seconds from an ffmpeg ``-progress`` line, or None."""
    for key, divisor in (("out_time_us=", 1_000_000.0), ("out_time_ms=", 1_000.0)):
        if line.startswith(key):
            try:
                return int(line[len(key):].strip()) / divisor
            except ValueError:
                return None
    return None


class PreprocessService:
    """Builds analysis assets for one project with FFmpeg."""

    def __init__(self, settings: Any, storage: StorageService) -> None:
        self._settings = settings
        self._storage = storage

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(
        self,
        project_row: dict[str, Any],
        ffmpeg_path: str,
        progress_callback: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        """Generate the three assets; return the project fields to store.

        Raises :class:`PreprocessError` (or ``FFmpegUnavailableError`` from
        callers) on any failure, after removing partially written files.
        """
        project_id = project_row["id"]
        source = Path(project_row["input_path"])
        if not source.is_file() or source.stat().st_size == 0:
            raise PreprocessError(
                f"Input video for project {project_id} is missing or empty; "
                "re-upload the video."
            )

        dirs = self._storage.ensure_project_dirs(project_id)
        analysis_out = dirs["analysis"] / "analysis.mp4"
        thumb_out = dirs["thumbnails"] / "poster.jpg"
        audio_out = dirs["audio"] / "audio.wav"
        outputs = [analysis_out, thumb_out] + (
            [audio_out] if project_row.get("has_audio") else []
        )

        self._report(progress_callback, _W_ANALYSIS[0])
        try:
            duration = float(project_row.get("duration") or 0.0)

            # 1) Analysis copy -------------------------------------------
            self._run_step(
                self._analysis_cmd(ffmpeg_path, source, analysis_out),
                duration=duration,
                window=_W_ANALYSIS,
                timeout=self._settings.preprocess_timeout_seconds,
                progress_callback=progress_callback,
                step="analysis copy",
            )
            analysis_dims = self._analysis_dimensions(project_row)

            # 2) Poster thumbnail ----------------------------------------
            seek = _clamp(duration * 0.1, 0.05, 10.0) if duration > 0 else 1.0
            self._run_step(
                self._thumbnail_cmd(ffmpeg_path, source, thumb_out, seek),
                duration=duration,
                window=_W_THUMB,
                timeout=self._settings.preprocess_timeout_seconds,
                progress_callback=progress_callback,
                step="thumbnail",
            )

            # 3) Audio extraction (optional) -----------------------------
            audio_path_rel: str | None = None
            if project_row.get("has_audio"):
                self._run_step(
                    self._audio_cmd(ffmpeg_path, source, audio_out),
                    duration=duration,
                    window=_W_AUDIO,
                    timeout=self._settings.preprocess_timeout_seconds,
                    progress_callback=progress_callback,
                    step="audio extraction",
                )
                audio_path_rel = "audio/audio.wav"

            for path, rel in ((analysis_out, "analysis/analysis.mp4"),
                              (thumb_out, "thumbnails/poster.jpg")):
                if not path.is_file() or path.stat().st_size == 0:
                    raise PreprocessError(
                        f"FFmpeg produced no {rel} - the source video may "
                        "be unreadable."
                    )
            if audio_path_rel and (not audio_out.is_file()
                                   or audio_out.stat().st_size == 0):
                raise PreprocessError(
                    "FFmpeg produced no audio/audio.wav although the source "
                    "reports an audio track."
                )

            self._report(progress_callback, _W_DONE)
            logger.info(
                "Preprocessing finished for project %s "
                "(analysis=%sx%s@%sfps, thumbnail=%s, audio=%s)",
                project_id, *analysis_dims,
                thumb_out.name, audio_out.name if audio_path_rel else "none",
            )
            return {
                "analysis_path": "analysis/analysis.mp4",
                "analysis_width": analysis_dims[0],
                "analysis_height": analysis_dims[1],
                "analysis_fps": analysis_dims[2],
                "thumbnail_path": "thumbnails/poster.jpg",
                "audio_path": audio_path_rel,
                "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }

        except PreprocessError:
            self._remove_outputs(outputs)
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            self._remove_outputs(outputs)
            raise PreprocessError(f"Preprocessing failed: {exc}") from exc

    # ------------------------------------------------------------------
    # FFmpeg command builders
    # ------------------------------------------------------------------
    def _analysis_cmd(
        self, ffmpeg: str, source: Path, out: Path
    ) -> list[str]:
        return [
            ffmpeg, "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
            "-i", str(source),
            "-map", "0:v:0",
            "-vf",
            f"scale='min({self._settings.analysis_width},iw)':-2,"
            f"fps={self._settings.analysis_fps}",
            "-c:v", "libx264",
            "-preset", self._settings.analysis_encoder_preset,
            "-crf", str(self._settings.analysis_crf),
            "-pix_fmt", "yuv420p",
            "-an",
            str(out),
        ]

    def _thumbnail_cmd(
        self, ffmpeg: str, source: Path, out: Path, seek: float
    ) -> list[str]:
        return [
            ffmpeg, "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
            "-ss", f"{seek:.3f}",
            "-i", str(source),
            "-frames:v", "1",
            "-vf", f"scale='min({self._settings.thumbnail_width},iw)':-2",
            str(out),
        ]

    def _audio_cmd(self, ffmpeg: str, source: Path, out: Path) -> list[str]:
        return [
            ffmpeg, "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
            "-i", str(source),
            "-map", "0:a:0",
            "-ac", str(self._settings.audio_channels),
            "-ar", str(self._settings.audio_sample_rate),
            "-c:a", "pcm_s16le",
            str(out),
        ]

    # ------------------------------------------------------------------
    # Execution + progress
    # ------------------------------------------------------------------
    def _run_step(
        self,
        cmd: list[str],
        *,
        duration: float,
        window: tuple[float, float],
        timeout: int,
        progress_callback: Callable[[float], None] | None,
        step: str,
    ) -> None:
        """Run one ffmpeg step, mapping elapsed media time to progress.

        Progress is honest: percent = window_start + (elapsed / duration) *
        window_width, reported monotonically (throttled to 0.5% steps).
        """
        flags: dict[str, object] = {}
        if os.name == "nt":  # pragma: no cover - Windows-only nicety
            flags["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **flags,  # type: ignore[arg-type]
            )
        except OSError as exc:
            raise PreprocessError(f"Could not start FFmpeg ({step}): {exc}") from exc

        deadline = time.monotonic() + timeout
        start = window[0]
        span = window[1] - window[0]
        last_reported = start
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                elapsed = _parse_out_time(line)
                if elapsed is None or duration <= 0:
                    continue
                pct = start + min(1.0, elapsed / duration) * span
                if pct - last_reported >= 0.5:
                    last_reported = pct
                    self._report(progress_callback, round(pct, 1))
            remaining = deadline - time.monotonic()
            proc.wait(timeout=max(0.1, remaining))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise PreprocessError(
                f"FFmpeg timed out after {timeout}s during {step}. The "
                "video may be too large for this machine; consider a "
                "smaller file or raising PREPROCESS_TIMEOUT_SECONDS."
            ) from None

        stderr = (proc.stderr.read() if proc.stderr else "") or ""
        if proc.returncode != 0:
            detail = stderr.strip().splitlines()[-1][:300] if stderr.strip() else "unknown error"
            raise PreprocessError(
                f"FFmpeg failed during {step}: {detail}"
            )
        self._report(progress_callback, window[1])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _analysis_dimensions(
        self, project_row: dict[str, Any]
    ) -> tuple[int, int, float]:
        """Deterministic output size of the analysis copy.

        ``scale='min(W,iw)':-2`` never upscales and rounds the height to the
        nearest even number; ``fps=N`` normalizes to exactly N.
        """
        src_w = int(project_row.get("width") or 0)
        src_h = int(project_row.get("height") or 0)
        if src_w <= 0 or src_h <= 0:
            raise PreprocessError(
                "Source dimensions are unknown; cannot build the analysis copy."
            )
        width = min(self._settings.analysis_width, src_w)
        height = int(2 * round(src_h * (width / src_w) / 2))
        height = max(2, height)
        return width, height, float(self._settings.analysis_fps)

    @staticmethod
    def _remove_outputs(outputs: list[Path]) -> None:
        for path in outputs:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:  # pragma: no cover - best-effort cleanup
                logger.warning("Could not remove partial asset %s: %s", path, exc)

    @staticmethod
    def _report(
        callback: Callable[[float], None] | None, progress: float
    ) -> None:
        if callback is not None:
            callback(progress)


__all__ = ["PreprocessService"]