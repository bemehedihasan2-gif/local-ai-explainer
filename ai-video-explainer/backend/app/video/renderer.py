"""Final video render (Phase 7).

The actual renderer lives in :class:`FinalVideoRenderer`; ``VideoRenderService``
remains only as the registered stub for ``PipelineStage.VIDEO_RENDER`` (the
real pipeline dispatches the composite ``final_render`` job to
``services/render.py``, which owns a ``FinalVideoRenderer``).

Pipeline (all streamed through FFmpeg - never loaded into RAM):

1. Extract every planned source clip with ``-ss``/``-t`` and normalize it to
   a common resolution/fps/pixfmt into H.264 MPEG-TS intermediates (tpad
   holds the last frame when the source is shorter than its window).
2. Build the original-audio track (one AAC slice per clip window, silence
   elsewhere) when ``ORIGINAL_AUDIO_ENABLED`` and the source has audio.
3. One final pass: concat video + original track + narration, mix with
   narration-driven sidechain ducking, burn subtitles (libass) and encode
   ``final.mp4`` with ``-movflags +faststart``.

Every subprocess uses an argument array (no shell), validated paths, a hard
timeout and captured stderr. FFmpeg machine-readable progress is parsed so
the UI reports real progress.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.utils.errors import RenderError, SubtitleFontMissingError
from app.utils.logging import get_logger

logger = get_logger("app.video.renderer")

_PROGRESS_RE = re.compile(r"out_time_ms=(\d+)|out_time_us=(\d+)")


def _spawn(*argv: str, timeout: int) -> subprocess.CompletedProcess[str]:
    flags: dict[str, object] = {}
    if os.name == "nt":  # pragma: no cover - Windows-only nicety
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **flags,  # type: ignore[arg-type]
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderError(
            "An FFmpeg render pass exceeded its timeout; the process was "
            "terminated. Retry the render (temp files are cleaned)."
        ) from exc
    except OSError as exc:
        raise RenderError(f"Could not run FFmpeg: {exc}") from exc


def _check(proc: subprocess.CompletedProcess[str], *, what: str) -> None:
    if proc.returncode != 0:
        raise RenderError(
            f"{what} failed: {(proc.stderr or '').strip()[:600]}"
        )


def _filter_escape(path: Path) -> str:
    """Escape a filesystem path for use inside one ffmpeg filtergraph
    token (single-quoted; backslashes become forward slashes)."""
    return "'" + str(path).replace("\\", "/").replace("'", "\\'") + "'"


class FinalVideoRenderer:
    """Builds ``final.mp4`` from a render plan (pure FFmpeg, CPU-first)."""

    def __init__(
        self,
        *,
        settings: Any,
        ffmpeg_path: str,
        ffprobe_path: str,
        progress_callback: Callable[[float, str | None], None] | None = None,
    ) -> None:
        self._settings = settings
        self._ffmpeg = ffmpeg_path
        self._ffprobe = ffprobe_path
        self._tick = progress_callback or (lambda _p, _s: None)

    # ------------------------------------------------------------------
    def _output_size(self, source_width: int, source_height: int) -> tuple[int, int]:
        max_w = int(self._settings.output_max_width)
        max_h = int(self._settings.output_max_height)
        width = source_width or max_w
        height = source_height or max_h
        if width <= max_w and height <= max_h:
            return width if width % 2 == 0 else width - 1, height if height % 2 == 0 else height - 1
        scale = min(max_w / width, max_h / height)
        out_w = max(2, int(width * scale / 2) * 2)
        out_h = max(2, int(height * scale / 2) * 2)
        return out_w, out_h

    def _fps_setting(self, source_fps: float) -> str:
        value = self._settings.output_fps
        if isinstance(value, str) and value == "source":
            fps = source_fps or 30.0
            return f"{fps:.3f}"
        return str(int(value) if isinstance(value, int) else value)

    # ------------------------------------------------------------------
    def render(
        self,
        *,
        source_path: Path,
        source_has_audio: bool,
        clips: list[dict[str, Any]],
        narration_path: Path,
        narration_rate: int,
        total_duration_ms: int,
        burn_srt: Path | None,
        burn_enabled: bool,
        language: str,
        work_dir: Path,
        final_path: Path,
        timeout: int,
    ) -> dict[str, Any]:
        """Execute the full render. ``final_path`` is only written after the
        final pass succeeds (tmp file then os.replace)."""
        work_dir.mkdir(parents=True, exist_ok=True)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self._tick(5.0, "preparing_render")

        # Output geometry from the first clip (all clips come from one source
        # so a single geometry is correct).
        width, height = self._output_size(
            int(self._settings.output_max_width), int(self._settings.output_max_height)
        )
        fps = self._fps_setting(0.0)
        scale = f"scale={width}:{height}"
        vf_base = [scale, f"fps={fps}", "format=yuv420p"]

        clip_files: list[Path] = []
        count = len(clips)
        for index, clip in enumerate(clips, start=1):
            self._tick(8.0 + 27.0 * (index - 1) / max(1, count), "extracting_clips")
            ss_ms = int(clip["source_start_ms"])
            length_ms = max(1, int(clip["source_end_ms"]) - ss_ms)
            total_ms = max(length_ms, int(clip["output_end_ms"]) - int(clip["output_start_ms"]))
            hold_s = max(0.0, (total_ms - length_ms) / 1000.0)
            out = work_dir / f"clip_{index:03d}.ts"
            vf = list(vf_base)
            if hold_s > 0.01:
                vf.append(f"tpad=stop_mode=clone:stop_duration={hold_s:.3f}")
            args = [
                self._ffmpeg, "-y", "-v", "error",
                "-ss", f"{ss_ms / 1000:.3f}",
                "-i", str(source_path),
                "-t", f"{total_ms / 1000:.3f}",
                "-vf", ",".join(vf),
                "-an", "-c:v", "libx264", "-preset", str(self._settings.video_preset),
                "-crf", str(self._settings.video_crf), "-pix_fmt", "yuv420p",
                str(out),
            ]
            _check(_spawn(*args, timeout=timeout), what=f"Clip {index} extraction")
            clip_files.append(out)
        self._tick(38.0, "assembling_video")

        concat_list = work_dir / "video_list.txt"
        concat_list.write_text(
            "".join(f"file '{path}'\n" for path in clip_files), encoding="utf-8"
        )
        video_ts = work_dir / "video_only.ts"
        _check(
            _spawn(
                self._ffmpeg, "-y", "-v", "error",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c", "copy", str(video_ts),
            ),
            what="Video assembly",
        )

        # ---- original audio track -------------------------------------
        audio_inputs: list[str] = []
        original_track: Path | None = None
        original_ok = (
            bool(self._settings.original_audio_enabled) and source_has_audio
        )
        if original_ok:
            self._tick(42.0, "preparing_original_audio")
            audio_slices: list[Path] = []
            for index, clip in enumerate(clips, start=1):
                ss_ms = int(clip["source_start_ms"])
                dur_ms = (
                    int(clip["output_end_ms"]) - int(clip["output_start_ms"])
                )
                slice_path = work_dir / f"orig_{index:03d}.m4a"
                args = [
                    self._ffmpeg, "-y", "-v", "error",
                    "-ss", f"{ss_ms / 1000:.3f}",
                    "-i", str(source_path),
                    "-t", f"{dur_ms / 1000:.3f}",
                    "-vn", "-af", "apad", "-ac", "2", "-ar", "48000",
                    "-c:a", "aac", "-b:a", "128k",
                    str(slice_path),
                ]
                _check(_spawn(*args, timeout=timeout), what="Original audio slice")
                audio_slices.append(slice_path)
            audio_list = work_dir / "audio_list.txt"
            audio_list.write_text(
                "".join(f"file '{path}'\n" for path in audio_slices),
                encoding="utf-8",
            )
            original_track = work_dir / "original_track.m4a"
            _check(
                _spawn(
                    self._ffmpeg, "-y", "-v", "error",
                    "-f", "concat", "-safe", "0", "-i", str(audio_list),
                    "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
                    str(original_track),
                ),
                what="Original audio assembly",
            )
        self._tick(48.0, "mixing_audio")

        # ---- subtitle burn ----------------------------------------------
        burn_filter = ""
        if burn_enabled and burn_srt is not None:
            burn_srt = Path(burn_srt)
            style = (
                f"FontSize={int(self._settings.subtitle_font_size)},"
                f"MarginV={int(self._settings.subtitle_margin_v)},"
                "BorderStyle=1,Outline=1.2,Shadow=0.5,"
                "PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000"
            )
            font_name = self._settings.subtitle_font_name
            font_path = self._settings.subtitle_font_path
            if font_name:
                style = f"FontName={font_name}," + style
            elif font_path and Path(font_path).is_file():
                style = f"FontName={Path(font_path).stem}," + style
            elif language in ("hi", "bn"):
                raise SubtitleFontMissingError(
                    "Hindi/Bengali burn-in requires SUBTITLE_FONT_PATH (or "
                    "SUBTITLE_FONT_NAME) - see the README Phase 7 setup."
                )
            burn_filter = (
                f"subtitles=filename={_filter_escape(burn_srt)}"
                f":force_style={_filter_escape(style)}"
            )

        # ---- final pass --------------------------------------------------
        self._tick(55.0, "rendering_final_video")
        total_s = total_duration_ms / 1000.0
        inputs: list[str] = [self._ffmpeg, "-y", "-v", "error", "-i", str(video_ts)]
        if original_track is not None:
            inputs += ["-i", str(original_track)]
        inputs += ["-i", str(narration_path)]

        narr_vol = float(self._settings.narration_audio_volume)
        orig_vol = float(self._settings.original_audio_volume)
        nar_index = 1 if original_track is None else 2
        orig_index = 1 if original_track is not None else None

        parts: list[str] = []
        # narration -> stereo 48k with configured volume
        parts.append(
            f"[{nar_index}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"volume={narr_vol}[narr]"
        )
        if orig_index is not None and orig_vol > 0:
            duck = (
                "1" if self._settings.audio_ducking_enabled else "0"
            )
            if duck == "1":
                parts.append(
                    f"[{orig_index}:a]volume={orig_vol}[orig];"
                    f"[orig][{nar_index}:a]sidechaincompress="
                    f"threshold={self._settings.audio_ducking_threshold}:"
                    f"ratio={self._settings.audio_ducking_ratio}:"
                    f"attack={int(self._settings.audio_ducking_attack_ms)}:"
                    f"release={int(self._settings.audio_ducking_release_ms)}[ducked];"
                    f"[narr][ducked]amix=inputs=2:duration=first:normalize=0,"
                    f"alimiter=limit=0.891[outa]"
                )
            else:
                parts.append(
                    f"[{orig_index}:a]volume={orig_vol},aresample=48000,"
                    f"aformat=channel_layouts=stereo[orig];"
                    f"[narr][orig]amix=inputs=2:duration=first:normalize=0,"
                    f"alimiter=limit=0.891[outa]"
                )
        else:
            parts.append("" if False else f"[narr]alimiter=limit=0.891[outa]")
        # narration alone also needs -t to allow tail silence? narration is
        # shorter than the video; amix duration=first ends at narration end
        # leaving the tail without audio -> pad narration to total.
        if orig_index is None or orig_vol == 0:
            # narration is the only audio: pad it so the tail has silence.
            parts[-1] = parts[-1].replace(
                "[narr]alimiter", "[narr]apad=whole_dur=" + f"{total_s:.3f}" + ",alimiter"
            )
        else:
            pass
        filter_complex = ";".join(parts)

        tmp_out = work_dir / "final_tmp.mp4"
        cmd = inputs + [
            "-filter_complex", filter_complex,
        ]
        if burn_filter:
            cmd += ["-vf", burn_filter]
        cmd += [
            "-map", "0:v", "-map", "[outa]",
            "-c:v", str(self._settings.video_codec),
            "-preset", str(self._settings.video_preset),
            "-crf", str(self._settings.video_crf),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000",
            "-movflags", "+faststart",
            "-t", f"{total_s:.3f}",
            str(tmp_out),
        ]
        proc = _spawn(*cmd, timeout=timeout)
        if proc.returncode != 0:
            raise RenderError(
                "Final encode failed: "
                f"{(proc.stderr or '').strip()[:600]}"
            )
        self._tick(92.0, "finalizing")
        tmp_out.replace(final_path)
        logger.info("Final render wrote %s (%d clips).", final_path, count)
        return {
            "clips_rendered": count,
            "width": width,
            "height": height,
            "fps": float(fps),
            "duration_ms": total_duration_ms,
        }


__all__ = ["FinalVideoRenderer", "VideoRenderService"]


# ----------------------------------------------------------------------
# Registered stub (kept for the ai/registry contract: execute() refuses).
# ----------------------------------------------------------------------
from app.ai.base import PipelineService  # noqa: E402
from app.models.enums import PipelineStage  # noqa: E402


class VideoRenderService(PipelineService):
    stage = PipelineStage.VIDEO_RENDER
    name = "Video Render"
    planned_for = "Phase 7"
