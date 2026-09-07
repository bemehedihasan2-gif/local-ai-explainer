"""Composite Phase 7 orchestrator: NARRATION_READY -> COMPLETED.

Reads Phase 5 ``analysis/story/selected_scenes.json`` (+ duration plan /
script as provenance), Phase 6 ``audio/narration_manifest.json`` and
``audio/narration_timeline.json``, plans the output timeline, renders
``output/final.mp4`` with mixed audio + burned subtitles, then QC's the
result and writes ``output/final_manifest.json`` + ``output/final_timeline.json``.

The job runs on the existing single worker (FIFO, concurrency 1); on any
failure the render artifacts are cleared and the project returns to
NARRATION_READY (RENDER_FAILED is set by the worker) so rendering can be
retried without repeating Phases 1-6.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services.final_qc import FinalVideoQCService
from app.services.render_plan import (
    build_video_plan,
    digest_document,
    plan_fingerprint,
)
from app.services.storage import StorageService
from app.utils.errors import RenderArtifactError, RenderError
from app.utils.logging import get_logger
from app.video.renderer import FinalVideoRenderer

logger = get_logger("app.services.render")

# Progress windows per stage (sums to 100).
STAGE_WINDOWS: dict[str, tuple[float, float]] = {
    "preparing_plan": (0.0, 6.0),
    "extracting_clips": (6.0, 34.0),
    "assembling_video": (34.0, 42.0),
    "preparing_original_audio": (42.0, 48.0),
    "mixing_audio": (48.0, 55.0),
    "rendering_final_video": (55.0, 92.0),
    "finalizing": (92.0, 96.0),
    "qc": (96.0, 100.0),
}

_STAGE_LABELS = {
    "preparing_plan": "Preparing render plan",
    "extracting_clips": "Extracting & normalizing scene clips",
    "assembling_video": "Assembling the video timeline",
    "preparing_original_audio": "Preparing original audio",
    "mixing_audio": "Mixing narration + original audio",
    "rendering_final_video": "Rendering final video (burning subtitles)",
    "finalizing": "Finalizing",
    "qc": "Validating final MP4",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RenderArtifactError(f"Missing render input: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


class RenderService:
    def __init__(self, settings, storage: StorageService) -> None:
        self._settings = settings
        self._storage = storage

    # -- artifact paths -------------------------------------------------
    def _story_file(self, project_id: str, name: str) -> Path:
        return self._storage.project_path(project_id, "analysis", "story", name)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def cleanup_artifacts(self, project_id: str) -> None:
        """Remove previous render outputs/plans/temp (never Phase 1-6 data)."""
        root = self._storage.project_root(project_id)
        for rel in (
            "render/temp", "render/video_plan.json", "render/burn.srt",
            "output/final.mp4", "output/final_manifest.json",
            "output/final_timeline.json", "output/final_qc.json",
        ):
            path = root / rel
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not clean %s: %s", path, exc)
        logger.info("Cleaned Phase 7 artifacts for project %s.", project_id)

    # -- main run --------------------------------------------------------
    def run(
        self,
        project: dict[str, Any],
        *,
        language: str,
        narration_fingerprint: str,
        render_fingerprint: str,
        ffmpeg_path: str,
        ffprobe_path: str,
        progress_callback: Callable[[float, str | None], None] | None = None,
    ) -> dict[str, Any]:
        project_id = str(project["id"])
        self._tick = progress_callback or (lambda _p, _s: None)
        self._tick(1.0, "preparing_plan")

        selected_doc = _read_json(self._story_file(project_id, "selected_scenes.json"))
        selected = selected_doc.get("selected") or selected_doc.get("scenes") or []
        duration_doc = _read_json(self._story_file(project_id, "duration_plan.json"))
        # Provenance: the script must exist too (its text is what got voiced).
        _read_json(self._story_file(project_id, "script.json"))

        manifest = _read_json(
            self._storage.project_path(project_id, "audio", "narration_manifest.json")
        )
        timeline = _read_json(
            self._storage.project_path(project_id, "audio", "narration_timeline.json")
        )
        segments = timeline.get("segments") or []
        narration_ms = int(
            manifest.get("generation", {}).get("duration_ms")
            or timeline.get("total_duration_ms")
            or 0
        )
        narration_wav = self._storage.project_path(project_id, "audio", "narration.wav")
        if not narration_wav.is_file():
            raise RenderArtifactError("narration.wav is missing - re-run narration.")

        plan = build_video_plan(
            selected,
            segments,
            narration_duration_ms=narration_ms,
            tail_ms=int(self._settings.render_tail_ms),
            hold_gap_max_ms=int(self._settings.render_hold_gap_max_ms),
        )
        plan_digest = plan_fingerprint(plan)
        self._write_json(
            self._storage.project_path(project_id, "render", "video_plan.json"), plan
        )
        self._tick(6.0, "extracting_clips")

        # Copy the SRT into render/temp with a plain name (libass filter
        # needs a simple, escape-free filename for the burn pass).
        srt_source = self._storage.project_path(project_id, "subtitles", "subtitles.srt")
        burn_srt: Path | None = None
        if srt_source.is_file():
            burn_srt = self._storage.project_path(project_id, "render", "burn.srt")
            burn_srt.write_bytes(srt_source.read_bytes())
            srt_ok = True
        else:
            srt_ok = False
            logger.warning("Sidecar SRT missing for project %s.", project_id)

        # Source geometry/fps come from the validated project metadata.
        source = self._storage.project_path(
            project_id, "input", str(project.get("stored_filename") or "")
        )
        if not source.is_file():
            raise RenderArtifactError("The source video file is missing.")
        source_fps = float(project.get("fps") or 30.0)

        renderer = FinalVideoRenderer(
            settings=self._settings,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            progress_callback=self._tick,
        )
        result = renderer.render(
            source_path=source,
            source_has_audio=bool(project.get("has_audio")),
            clips=plan["clips"],
            narration_path=narration_wav,
            narration_rate=int(manifest.get("generation", {}).get("sample_rate") or 22050),
            total_duration_ms=int(plan["video_duration_ms"]),
            burn_srt=burn_srt,
            burn_enabled=bool(self._settings.subtitle_burn_enabled) and srt_ok,
            language=language,
            work_dir=self._storage.project_path(project_id, "render", "temp"),
            final_path=self._storage.project_path(project_id, "output", "final.mp4"),
            timeout=int(self._settings.render_timeout_seconds),
        )
        self._tick(94.0, "qc")

        qc = FinalVideoQCService(ffprobe_bin=ffprobe_path, ffmpeg_bin=ffmpeg_path)
        final_path = self._storage.project_path(project_id, "output", "final.mp4")
        qc_report = qc.inspect(
            final_path,
            expected_duration_ms=int(plan["video_duration_ms"]),
            narration_duration_ms=int(narration_ms),
            narration_expected=True,
            subtitle_sidecar=srt_source if srt_source.is_file() else None,
            burn_enabled=bool(self._settings.subtitle_burn_enabled),
        )
        self._write_json(
            self._storage.project_path(project_id, "output", "final_qc.json"),
            qc_report,
        )

        # output/final_timeline.json - canonical mapping for debugging.
        final_timeline = {
            "schema_version": 1,
            "requested_duration_seconds": (
                int(project.get("target_duration_seconds") or 180)
            ),
            "narration_duration_ms": narration_ms,
            "video_duration_ms": int(plan["video_duration_ms"]),
            "subtitle_aligned": plan["subtitle_aligned"],
            "segments": [
                {
                    "output_start_ms": clip["output_start_ms"],
                    "output_end_ms": clip["output_end_ms"],
                    "scene_ids": [clip["scene_id"]],
                    "narration_segment_ids": _narration_segment_ids(clip, plan),
                    "subtitle_ids": _narration_segment_ids(clip, plan),
                }
                for clip in plan["clips"]
            ],
        }
        self._write_json(
            self._storage.project_path(project_id, "output", "final_timeline.json"),
            final_timeline,
        )

        size = final_path.stat().st_size
        manifest_payload = {
            "schema_version": 1,
            "project_id": project_id,
            "created_at": _now(),
            "source": {
                "sha256": project.get("sha256"),
                "original_filename": project.get("original_filename"),
            },
            "fingerprints": {
                "narration": narration_fingerprint,
                "render": render_fingerprint,
            },
            "generation": {
                "language": language,
                "requested_duration_seconds": int(
                    project.get("target_duration_seconds") or 180
                ),
                "narration_duration_ms": narration_ms,
                "final_duration_ms": int(plan["video_duration_ms"]),
            },
            "media": {
                "path": "output/final.mp4",
                "duration_ms": qc_report["probe"]["duration_ms"],
                "width": result.get("width"),
                "height": result.get("height"),
                "fps": result.get("fps"),
                "size_bytes": size,
                "video_codec": (qc_report["probe"].get("video") or {}).get("codec"),
                "audio_codec": (qc_report["probe"].get("audio") or {}).get("codec"),
            },
            "subtitles": {
                "burned": bool(self._settings.subtitle_burn_enabled) and srt_ok,
                "sidecar_srt": "subtitles/subtitles.srt" if srt_ok else None,
            },
            "quality": qc_report,
            "plan": {
                "path": "render/video_plan.json",
                "digest": plan_digest,
            },
            "warnings": list(plan.get("warnings", [])),
        }
        self._write_json(
            self._storage.project_path(project_id, "output", "final_manifest.json"),
            manifest_payload,
        )

        # Remove intermediates (keep only plan/timeline/manifest/final.mp4).
        temp_dir = self._storage.project_path(project_id, "render", "temp")
        shutil.rmtree(temp_dir, ignore_errors=True)

        self._tick(100.0, None)
        logger.info(
            "Render completed for project %s (QC=%s, %d bytes).",
            project_id, qc_report["quality_score"], size,
        )
        return {
            "output_path": "output/final.mp4",
            "output_duration_ms": qc_report["probe"]["duration_ms"],
            "output_width": result.get("width"),
            "output_height": result.get("height"),
            "output_fps": result.get("fps"),
            "output_size_bytes": size,
            "qc_score": qc_report["quality_score"],
            "subtitle_status": (
                "burned" if manifest_payload["subtitles"]["burned"]
                else "sidecar_only"
            ),
            "warnings": list(manifest_payload["warnings"]),
        }


def _narration_segment_ids(clip: dict[str, Any], plan: dict[str, Any]) -> list[int]:
    """The narration segment ids that play while ``clip`` is on screen."""
    out_start = int(clip["output_start_ms"])
    out_end = int(clip["output_end_ms"])
    ids: list[int] = []
    for group in plan.get("groups", []):
        g_start = int(group.get("output_start_ms", 0))
        g_end = int(group.get("output_end_ms", 0))
        if g_end <= out_start or g_start >= out_end:
            continue
        if clip.get("scene_id") in group.get("scene_ids", []):
            ids.extend(group.get("narration_segment_ids", []))
    return sorted(set(ids))


__all__ = ["RenderService", "STAGE_WINDOWS", "_STAGE_LABELS"]
