"""Composite analysis job (Phase 4): PREPARED -> ANALYZED.

Runs sequentially inside the existing single worker thread:

    PREPARING -> SCENE_DETECTION -> SPEECH_TO_TEXT -> OCR ->
    VISUAL_ANALYSIS -> TIMELINE -> QUALITY_CHECK -> FINALIZING

Artifacts (all under ``analysis/`` in the project folder, relative paths
only in every document):

    metadata/scenes.json  metadata/transcript.json  metadata/ocr.json
    metadata/visual.json  metadata/timeline.json   metadata/analysis_manifest.json
    frames/scene_%03d.jpg  (representative frames)

Stage policy: scene detection is structural (a failure fails the job and
returns the project to PREPARED); STT/OCR/visual stages degrade gracefully
(unavailable packages/models produce ``*_available=false`` + a warning and
the rest of the pipeline still runs). Progress uses fixed stage windows and
real per-stage progress (ffmpeg elapsed time, whisper segment time, OCR /
visual frames processed) - never invented percentages.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.ai.ocr import OCRService, TesseractUnavailableError
from app.ai.stt import SpeechToTextService, WhisperModelMissingError, WhisperUnavailableError
from app.ai.vision import VisionAnalysisService
from app.services.storage import StorageService
from app.services.timeline import build_timeline, quality_check
from app.utils.errors import AnalysisError, SceneDetectionError
from app.utils.fingerprints import analysis_config_fingerprint, preprocessing_fingerprint
from app.utils.logging import get_logger
from app.video.scenes import detect_scenes, extract_representative_frames

logger = get_logger("app.services.analysis")

#: Global progress windows (start, end) per stage - honest stage-based.
WINDOWS: dict[str, tuple[float, float]] = {
    "preparing": (0.0, 5.0),
    "scene_detection": (5.0, 25.0),
    "speech_to_text": (25.0, 50.0),
    "ocr": (50.0, 70.0),
    "visual_analysis": (70.0, 82.0),
    "timeline": (82.0, 92.0),
    "quality_check": (92.0, 97.0),
    "finalizing": (97.0, 100.0),
}

_STAGE_LABELS = {
    "preparing": "Preparing analysis",
    "scene_detection": "Scene detection",
    "speech_to_text": "Speech recognition",
    "ocr": "OCR",
    "visual_analysis": "Visual analysis",
    "timeline": "Timeline building",
    "quality_check": "Quality check",
    "finalizing": "Finalizing",
}


class AnalysisService:
    """Runs the Phase 4 analysis pipeline for one project (worker thread)."""

    def __init__(
        self,
        settings: Any,
        storage: StorageService,
        stt: SpeechToTextService | None = None,
        ocr: OCRService | None = None,
        vision: VisionAnalysisService | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._stt = stt or SpeechToTextService(settings)
        self._ocr = ocr or OCRService(settings)
        self._vision = vision or VisionAnalysisService(settings)

    # ------------------------------------------------------------------
    def run(
        self,
        project_row: dict[str, Any],
        ffmpeg_path: str,
        progress_callback: Callable[[float, str | None], None] | None = None,
    ) -> dict[str, Any]:
        """Analyze a PREPARED project; returns the summary for SQLite.

        Raises :class:`AnalysisError` (structural failure) after removing
        partial Phase 4 artifacts. Stage-level degradations (missing Whisper
        model, missing Tesseract, corrupt audio, ...) become warnings.
        """
        project_id = project_row["id"]
        started = time.monotonic()
        warnings: list[str] = []
        dirs = self._storage.ensure_project_dirs(project_id)
        metadata_dir = dirs["analysis"] / "metadata"
        frames_dir = dirs["analysis"] / "frames"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        frames_dir.mkdir(parents=True, exist_ok=True)

        analysis_path = dirs["analysis"] / "analysis.mp4"
        if not analysis_path.is_file():
            raise AnalysisError(
                "Analysis assets are missing - re-run preprocessing first."
            )

        try:
            # ---- SCENE DETECTION (structural) -------------------------
            self._tick(progress_callback, WINDOWS["preparing"][0], "preparing")
            scenes = self._run_scenes(
                project_row, ffmpeg_path, analysis_path, frames_dir,
                metadata_dir, progress_callback, warnings,
            )
            scene_count = len(scenes)

            # ---- SPEECH-TO-TEXT (graceful) -----------------------------
            transcript, stt_warnings, stt_ok = self._run_stt(
                project_row, dirs, metadata_dir, progress_callback,
            )
            warnings.extend(stt_warnings)
            self._stt.release_model()  # RAM back before OCR/visual

            # ---- OCR (graceful) ----------------------------------------
            ocr_results, ocr_ok, ocr_warnings = self._run_ocr(
                scenes, metadata_dir, progress_callback,
            )
            warnings.extend(ocr_warnings)

            # ---- VISUAL ANALYSIS (graceful) ----------------------------
            visual, visual_ok, visual_warnings = self._run_visual(
                scenes, metadata_dir, progress_callback,
            )
            warnings.extend(visual_warnings)

            # ---- TIMELINE + QUALITY CHECK ------------------------------
            self._tick(progress_callback, WINDOWS["timeline"][0], "timeline")
            duration = float(project_row.get("duration") or 0.0)
            timeline = build_timeline(
                scenes=scenes,
                transcript=transcript,
                ocr_results=ocr_results,
                visual_results=visual,
                duration_seconds=duration,
            )
            self._write_json(metadata_dir / "timeline.json", timeline)

            self._tick(progress_callback, WINDOWS["quality_check"][0], "quality_check")
            warnings.extend(
                quality_check(
                    scenes=scenes, transcript=transcript,
                    ocr_results=ocr_results, timeline=timeline,
                    duration_seconds=duration,
                )
            )

            # ---- MANIFEST -----------------------------------------------
            self._tick(progress_callback, WINDOWS["finalizing"][0], "finalizing")
            processing_seconds = round(time.monotonic() - started, 2)
            manifest = self._build_manifest(
                project_row, scenes, transcript, ocr_ok, visual, warnings,
                processing_seconds,
            )
            self._write_json(metadata_dir / "analysis_manifest.json", manifest)

            summary = {
                "scene_count": scene_count,
                "transcript_available": stt_ok,
                "detected_language": (transcript or {}).get("language"),
                "language_probability": (transcript or {}).get("language_probability"),
                "ocr_available": ocr_ok,
                "visual_provider": visual.get("provider", "deterministic"),
                "processing_seconds": processing_seconds,
                "warnings": warnings,
            }
            self._tick(progress_callback, 100.0, "finalizing")
            logger.info(
                "Analysis complete for project %s: %d scenes, stt=%s, ocr=%s.",
                project_id, scene_count, stt_ok, ocr_ok,
            )
            return summary

        except SceneDetectionError:
            raise
        except AnalysisError:
            raise
        except Exception as exc:  # noqa: BLE001 - structural failure
            logger.exception("Analysis failed for project %s.", project_id)
            raise AnalysisError(f"Analysis failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------
    def _run_scenes(
        self, project_row, ffmpeg_path, analysis_path, frames_dir,
        metadata_dir, progress_callback, warnings,
    ) -> list[dict[str, Any]]:
        self._tick(progress_callback, WINDOWS["scene_detection"][0], "scene_detection")
        duration = float(project_row.get("duration") or 0.0)
        scenes = detect_scenes(
            ffmpeg_path, analysis_path,
            threshold=self._settings.scene_threshold,
            min_duration=self._settings.min_scene_duration_seconds,
            max_scenes=self._settings.max_scenes,
            duration=duration,
            timeout_seconds=self._settings.analysis_timeout_seconds,
            progress_callback=self._windowed(progress_callback, "scene_detection"),
        )
        if not scenes:
            raise SceneDetectionError("Scene detection produced no scenes.")
        if len(scenes) == 1:
            warnings.append(
                f"Only one scene detected (threshold={self._settings.scene_threshold}); "
                "the video may be visually static."
            )

        frame_map = extract_representative_frames(
            ffmpeg_path, analysis_path, frames_dir, scenes,
            analysis_fps=float(project_row.get("analysis_fps") or self._settings.analysis_fps),
            timeout_seconds=self._settings.analysis_timeout_seconds,
            progress_callback=self._windowed(progress_callback, "scene_detection"),
        )
        for scene in scenes:
            scene["representative_frame"] = frame_map.get(scene["scene_id"])
        self._write_json(metadata_dir / "scenes.json", {
            "schema_version": 1,
            "duration_seconds": duration,
            "scenes": scenes,
        })
        return scenes

    def _run_stt(
        self, project_row, dirs, metadata_dir, progress_callback,
    ) -> tuple[dict[str, Any] | None, list[str], bool]:
        self._tick(progress_callback, WINDOWS["speech_to_text"][0], "speech_to_text")
        if not project_row.get("has_audio"):
            logger.info("No audio track; speech-to-text skipped.")
            return None, ["Speech-to-text skipped: the video has no audio (SKIPPED_NO_AUDIO)."], False
        wav = dirs["audio"] / "audio.wav"
        if not wav.is_file() or wav.stat().st_size == 0:
            return None, ["Speech-to-text skipped: audio.wav is missing."], False
        try:
            transcript = self._stt.transcribe(
                wav,
                preferred_language=project_row.get("language"),
                duration_seconds=float(project_row.get("duration") or 0.0),
                progress_callback=self._windowed(progress_callback, "speech_to_text"),
            )
            self._write_json(metadata_dir / "transcript.json", transcript)
            return transcript, transcript.get("warnings", []), True
        except (WhisperUnavailableError, WhisperModelMissingError) as exc:
            logger.warning("STT unavailable: %s", exc.message)
            return None, [exc.message], False
        except Exception as exc:  # noqa: BLE001 - graceful stage failure
            logger.exception("Transcription failed; continuing without speech.")
            return None, [f"Speech-to-text failed: {exc}"], False

    def _run_ocr(
        self, scenes, metadata_dir, progress_callback,
    ) -> tuple[list[dict[str, Any]], bool, list[str]]:
        self._tick(progress_callback, WINDOWS["ocr"][0], "ocr")
        if not self._settings.ocr_enabled:
            return [], False, ["OCR disabled by configuration (OCR_ENABLED=false)."]
        frames_dir = metadata_dir.parent / "frames"
        frames = [
            {
                "timestamp": s["representative_timestamp"],
                "path": str(frames_dir / Path(s["representative_frame"]).name),
            }
            for s in scenes if s.get("representative_frame")
        ][: self._settings.ocr_frame_limit]
        if not frames:
            return [], False, ["OCR skipped: no representative frames available."]
        try:
            results = self._ocr.run_on_frames(
                frames,
                progress_callback=self._windowed(progress_callback, "ocr"),
            )
            self._write_json(metadata_dir / "ocr.json", {
                "schema_version": 1,
                "frames": results,
            })
            return results, True, []
        except TesseractUnavailableError as exc:
            logger.warning("OCR unavailable: %s", exc.message)
            return [], False, [exc.message]
        except Exception as exc:  # noqa: BLE001 - graceful stage failure
            logger.exception("OCR failed; continuing without it.")
            return [], False, [f"OCR failed: {exc}"]

    def _run_visual(
        self, scenes, metadata_dir, progress_callback,
    ) -> tuple[dict[str, Any], bool, list[str]]:
        self._tick(progress_callback, WINDOWS["visual_analysis"][0], "visual_analysis")
        if not self._settings.visual_analysis_enabled:
            return {"provider": "disabled", "frames": []}, False, \
                ["Visual analysis disabled by configuration (VISUAL_ANALYSIS_ENABLED=false)."]
        frames_dir = metadata_dir.parent / "frames"
        frames = [
            {"timestamp": s["representative_timestamp"],
             "path": str(frames_dir / Path(s["representative_frame"]).name),
             "rel_path": s.get("representative_frame")}
            for s in scenes if s.get("representative_frame")
        ]
        if not frames:
            return {"provider": self._vision.provider_name, "frames": []}, False, \
                ["Visual analysis skipped: no representative frames available."]
        try:
            results = self._vision.analyze_frames(
                frames,
                progress_callback=self._windowed(progress_callback, "visual_analysis"),
            )
            self._write_json(metadata_dir / "visual.json", results)
            return results, True, []
        except Exception as exc:  # noqa: BLE001 - graceful stage failure
            logger.exception("Visual analysis failed; continuing without it.")
            return {"provider": self._vision.provider_name, "frames": []}, False, \
                [f"Visual analysis failed: {exc}"]

    # ------------------------------------------------------------------
    # Manifest + helpers
    # ------------------------------------------------------------------
    def _build_manifest(
        self, project_row, scenes, transcript, ocr_ok, visual, warnings,
        processing_seconds,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {
                "sha256": project_row.get("sha256"),
                "original_filename": project_row.get("original_filename"),
                "duration_seconds": project_row.get("duration"),
            },
            "preprocessing": {
                "analysis_path": "analysis/analysis.mp4",
                "analysis_width": project_row.get("analysis_width"),
                "analysis_height": project_row.get("analysis_height"),
                "analysis_fps": project_row.get("analysis_fps"),
                "preprocessing_fingerprint": preprocessing_fingerprint(project_row),
            },
            "configuration": {
                "fingerprint": analysis_config_fingerprint(self._settings),
                "settings": {
                    "scene_threshold": self._settings.scene_threshold,
                    "min_scene_duration_seconds": self._settings.min_scene_duration_seconds,
                    "max_scenes": self._settings.max_scenes,
                    "whisper_model": self._settings.whisper_model,
                    "whisper_device": self._settings.whisper_device,
                    "whisper_compute_type": self._settings.whisper_compute_type,
                    "whisper_language_mode": self._settings.whisper_language_mode,
                    "ocr_enabled": self._settings.ocr_enabled,
                    "ocr_frame_limit": self._settings.ocr_frame_limit,
                    "visual_analysis_enabled": self._settings.visual_analysis_enabled,
                },
            },
            "results": {
                "scene_count": len(scenes),
                "transcript_available": transcript is not None,
                "detected_language": (transcript or {}).get("language"),
                "language_probability": (transcript or {}).get("language_probability"),
                "ocr_available": ocr_ok,
                "visual_provider": visual.get("provider", "deterministic"),
                "processing_seconds": processing_seconds,
                "assets": {
                    "scenes": "analysis/metadata/scenes.json",
                    "transcript": "analysis/metadata/transcript.json" if transcript else None,
                    "ocr": "analysis/metadata/ocr.json" if ocr_ok else None,
                    "visual": "analysis/metadata/visual.json",
                    "timeline": "analysis/metadata/timeline.json",
                    "manifest": "analysis/metadata/analysis_manifest.json",
                    "frames_dir": "analysis/frames",
                },
            },
            "warnings": warnings,
        }

    @staticmethod
    def _write_json(path: Path, document: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _windowed(
        self,
        callback: Callable[[float, str | None], None] | None,
        stage: str,
    ) -> Callable[[float], None] | None:
        """Map a stage-local 0..1 fraction into the stage's global window."""
        if callback is None:
            return None
        start, end = WINDOWS[stage]

        def inner(fraction: float) -> None:
            callback(round(start + min(1.0, max(0.0, fraction)) * (end - start), 1), stage)

        return inner

    @staticmethod
    def _tick(
        callback: Callable[[float, str | None], None] | None,
        progress: float,
        stage: str,
    ) -> None:
        if callback is not None:
            callback(progress, stage)

    # ------------------------------------------------------------------
    def cleanup_artifacts(self, project_id: str) -> None:
        """Remove Phase 4 artifacts (never Phase 3 assets).

        Called when a fresh analysis starts (stale config) and on failure.
        The ``analysis/`` folder also holds the Phase 3 analysis copy, so
        only ``metadata/`` and ``frames/`` are cleared.
        """
        try:
            root = self._storage.project_root(project_id)
            for sub in ("metadata", "frames"):
                folder = root / "analysis" / sub
                if not folder.is_dir():
                    continue
                for child in folder.iterdir():
                    try:
                        if child.is_file():
                            child.unlink()
                    except OSError as exc:  # pragma: no cover - best-effort
                        logger.warning("Could not remove %s: %s", child, exc)
            logger.info("Cleared Phase 4 artifacts for project %s.", project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Artifact cleanup incomplete for %s: %s", project_id, exc)


__all__ = ["AnalysisService", "WINDOWS", "_STAGE_LABELS"]