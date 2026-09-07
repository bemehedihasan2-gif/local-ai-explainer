"""Composite Phase 6 job orchestrator: SCRIPT_READY -> NARRATION_READY.

Runs sequentially inside the existing single worker thread:

    PREPARING_SCRIPT -> SEGMENTING -> CHECKING_VOICE -> GENERATING_VOICE ->
    MEASURING_AUDIO -> BUILDING_TIMELINE -> GENERATING_SUBTITLES ->
    ASSEMBLING -> AUDIO_QC -> FINALIZING

Artifacts (all under the project folder; relative paths only in every
document - never absolute filesystem paths):

    audio/segments/segment_%03d.wav   (per narration unit, measured)
    audio/segments.json               (segment metadata: text/scene ids/timing)
    audio/narration_timeline.json     (start/end ms per segment + gaps)
    audio/narration.wav               (assembled + normalized mono narration)
    audio/narration_quality.json      (deterministic QC report + score)
    audio/narration_manifest.json     (fingerprints, provider, paths)
    subtitles/subtitles.srt           (UTF-8, actual TTS timing)
    subtitles/subtitles.vtt

Design rules for the 8 GB target:

- one TTS subprocess at a time, per segment; the engine is never kept
  resident and segments are never synthesized concurrently;
- audio is measured from the actual generated WAV files (never estimated
  from word counts) and validated before it is accepted;
- segments are assembled and normalized with streaming stdlib WAV I/O, so
  Python memory stays flat regardless of narration length;
- if any stage fails the run fails cleanly, partial narration artifacts
  are removed, and the project returns to SCRIPT_READY for a retry.
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.ai.tts import (
    LocalTTSProvider,
    TTSEngineUnavailableError,
    TTSVoiceMissingError,
    build_tts_provider,
)
from app.services.narration_audio import (
    assemble_narration,
    normalize_loudness,
    read_wav_info,
    validate_segment_wav,
)
from app.services.narration_qc import run_quality_check
from app.services.segmentation import segment_script
from app.services.storage import StorageService
from app.services.subtitles import build_srt, build_vtt
from app.utils.errors import NarrationError
from app.utils.logging import get_logger

logger = get_logger("app.services.narration")

#: Global progress windows (start, end) per stage - honest stage-based.
WINDOWS: dict[str, tuple[float, float]] = {
    "preparing_script": (0.0, 6.0),
    "segmenting": (6.0, 10.0),
    "checking_voice": (10.0, 14.0),
    "generating_voice": (14.0, 68.0),
    "measuring_audio": (68.0, 74.0),
    "building_timeline": (74.0, 80.0),
    "generating_subtitles": (80.0, 86.0),
    "assembling": (86.0, 92.0),
    "audio_qc": (92.0, 97.0),
    "finalizing": (97.0, 100.0),
}

_STAGE_LABELS = {
    "preparing_script": "Preparing script",
    "segmenting": "Segmenting narration",
    "checking_voice": "Checking voice",
    "generating_voice": "Generating voice",
    "measuring_audio": "Measuring audio",
    "building_timeline": "Building narration timeline",
    "generating_subtitles": "Generating subtitles",
    "assembling": "Assembling narration",
    "audio_qc": "Audio quality check",
    "finalizing": "Finalizing",
}

#: Narration file names removed by :meth:`cleanup_artifacts` (the Phase 3
#: extracted ``audio/audio.wav`` is never touched).
_NARRATION_FILES = (
    "segments.json",
    "narration_timeline.json",
    "narration.wav",
    "narration_quality.json",
    "narration_manifest.json",
)


class NarrationService:
    """Runs the Phase 6 narration pipeline for one project (worker thread)."""

    def __init__(
        self,
        settings: Any,
        storage: StorageService,
        provider: LocalTTSProvider | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._provider = provider or build_tts_provider(settings)

    # ------------------------------------------------------------------
    def run(
        self,
        project_row: dict[str, Any],
        *,
        language: str,
        voice_id: str | None = None,
        script_fingerprint: str | None = None,
        generation_fingerprint: str | None = None,
        progress_callback: Callable[[float, str | None], None] | None = None,
    ) -> dict[str, Any]:
        """Synthesize narration for a SCRIPT_READY project.

        Returns the summary for SQLite. Raises on any structural failure
        (the worker then records it, cleans partial artifacts and returns
        the project to SCRIPT_READY).
        """
        project_id = project_row["id"]
        started = time.monotonic()
        warnings: list[str] = []
        dirs = self._storage.ensure_project_dirs(project_id)
        audio_dir = dirs["audio"]
        segments_dir = audio_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        subtitles_dir = dirs["project"] / "subtitles"
        subtitles_dir.mkdir(parents=True, exist_ok=True)

        try:
            # ---- PREPARE --------------------------------------------------
            self._tick(progress_callback, WINDOWS["preparing_script"][0], "preparing_script")
            script_path = dirs["analysis"] / "story" / "script.json"
            if not script_path.is_file():
                raise NarrationError(
                    "The explanation script is missing. Run script generation "
                    "first (status becomes SCRIPT_READY) before narration."
                )
            script_doc = json.loads(script_path.read_text(encoding="utf-8"))
            if script_doc.get("language") != language:
                warnings.append(
                    "The script on disk is in a different language than the "
                    "requested narration; synthesizing the requested language "
                    "with the requested voice."
                )

            # ---- SEGMENT ----------------------------------------------------
            self._tick(progress_callback, WINDOWS["segmenting"][0], "segmenting")
            segmentation = segment_script(
                script_doc,
                max_segment_chars=self._settings.narration_max_segment_chars,
                max_segment_words=self._settings.narration_max_segment_words,
            )
            warnings.extend(segmentation["warnings"])
            if not segmentation["segments"]:
                raise NarrationError(
                    "The script produced no narration segments - it is empty."
                )

            # ---- VOICE CHECK -------------------------------------------------
            self._tick(progress_callback, WINDOWS["checking_voice"][0], "checking_voice")
            report = self._provider.describe()
            languages = report.get("languages") or {}
            language_status = languages.get(language)
            if not report.get("executable_available"):
                raise TTSEngineUnavailableError(
                    report.get("setup_hint")
                    or "The local TTS engine is not available."
                )
            if not language_status or not language_status.get("available"):
                raise TTSVoiceMissingError(
                    language_status.get("note")
                    or f"No voice available for language '{language}'."
                )
            resolved_voice_id = (
                voice_id
                or (language_status or {}).get("voice_id")
                or language
            )
            logger.info(
                "Narration start: project=%s lang=%s voice=%s segments=%d",
                project_id, language, resolved_voice_id, segmentation["count"],
            )

            # ---- SYNTHESIZE (one subprocess at a time) ------------------------
            self._tick(progress_callback, WINDOWS["generating_voice"][0], "generating_voice")
            segments = segmentation["segments"]
            segment_meta: list[dict[str, Any]] = []
            canonical_rate: int | None = None
            for index, segment in enumerate(segments):
                seg_path = segments_dir / f"segment_{segment['segment_id']:03d}.wav"
                facts = self._provider.synthesize(
                    segment["text"], language, output_path=seg_path,
                )
                info = validate_segment_wav(seg_path)
                if canonical_rate is None:
                    canonical_rate = info["sample_rate"]
                    if info["sample_rate"] != self._settings.tts_sample_rate:
                        warnings.append(
                            f"The voice's actual sample rate "
                            f"({info['sample_rate']} Hz) differs from the "
                            f"configured TTS_SAMPLE_RATE "
                            f"({self._settings.tts_sample_rate} Hz); the "
                            "actual rate is used for the whole narration."
                        )
                else:
                    validate_segment_wav(
                        seg_path, expected_rate=canonical_rate,
                        expected_channels=self._settings.tts_channels,
                    )
                if facts.get("sample_rate") != info["sample_rate"]:
                    warnings.append(
                        f"Segment {segment['segment_id']}: the provider "
                        "reported a different sample rate than the file on "
                        "disk; the file was measured instead."
                    )
                segment_meta.append({
                    "segment_id": segment["segment_id"],
                    "sequence": segment["sequence"],
                    "text": segment["text"],
                    "words": segment["words"],
                    "scene_ids": segment["scene_ids"],
                    "section": segment["section"],
                    "section_index": segment["section_index"],
                    "audio_path": f"audio/segments/{seg_path.name}",
                    "sample_rate": info["sample_rate"],
                    "channels": info["channels"],
                    "duration_ms": info["duration_ms"],
                })
                fraction = (index + 1) / len(segments)
                self._tick(
                    progress_callback,
                    WINDOWS["generating_voice"][0]
                    + fraction * (
                        WINDOWS["generating_voice"][1]
                        - WINDOWS["generating_voice"][0]
                    ),
                    "generating_voice",
                )

            self._write_json(audio_dir / "segments.json", {
                "schema_version": 1,
                "language": language,
                "count": len(segment_meta),
                "segments": segment_meta,
                "warnings": warnings,
            })

            # ---- MEASURE -------------------------------------------------------
            self._tick(progress_callback, WINDOWS["measuring_audio"][0], "measuring_audio")
            total_segment_ms = sum(s["duration_ms"] for s in segment_meta)
            if total_segment_ms <= 0:
                raise NarrationError(
                    "All narration segments measured 0 ms - the TTS engine "
                    "produced silent audio."
                )
            logger.info(
                "Synthesized %d segments totaling %d ms (rate=%s).",
                len(segment_meta), total_segment_ms,
                canonical_rate or self._settings.tts_sample_rate,
            )

            # ---- TIMELINE -------------------------------------------------------
            self._tick(progress_callback, WINDOWS["building_timeline"][0], "building_timeline")
            timeline, assembly_entries = self._build_timeline(
                project_id, segment_meta,
                canonical_rate or self._settings.tts_sample_rate,
            )
            self._write_json(audio_dir / "narration_timeline.json", timeline)

            # ---- SUBTITLES --------------------------------------------------------
            self._tick(progress_callback, WINDOWS["generating_subtitles"][0], "generating_subtitles")
            timeline_segments = timeline["segments"]
            srt_text = build_srt(
                timeline_segments,
                max_chars_per_caption=self._settings.subtitle_max_chars_per_caption,
                max_chars_per_line=self._settings.subtitle_max_chars_per_line,
            )
            vtt_text = build_vtt(
                timeline_segments,
                max_chars_per_caption=self._settings.subtitle_max_chars_per_caption,
                max_chars_per_line=self._settings.subtitle_max_chars_per_line,
            )
            (subtitles_dir / "subtitles.srt").write_text(srt_text, encoding="utf-8")
            (subtitles_dir / "subtitles.vtt").write_text(vtt_text, encoding="utf-8")

            # ---- ASSEMBLE -----------------------------------------------------
            self._tick(progress_callback, WINDOWS["assembling"][0], "assembling")
            narration_path = audio_dir / "narration.wav"
            audio_facts = assemble_narration(
                assembly_entries,
                narration_path,
                sample_rate=canonical_rate or self._settings.tts_sample_rate,
                channels=self._settings.tts_channels,
            )
            norm_result = normalize_loudness(
                narration_path,
                target_mean_db=self._settings.audio_target_mean_db,
                max_gain_db=self._settings.audio_max_gain_db,
                peak_ceiling_db=self._settings.audio_peak_ceiling_db,
            )
            final_facts = read_wav_info(narration_path)

            # ---- QC ----------------------------------------------------------
            self._tick(progress_callback, WINDOWS["audio_qc"][0], "audio_qc")
            quality = run_quality_check(
                self._settings,
                timeline=timeline,
                segments_doc={
                    "segments": segment_meta,
                    "count": len(segment_meta),
                },
                script_doc=script_doc,
                wav_path=narration_path,
                srt_path=subtitles_dir / "subtitles.srt",
                expected_sample_rate=canonical_rate,
            )
            warnings.extend(quality.get("warnings", []))
            self._write_json(audio_dir / "narration_quality.json", quality)

            # ---- MANIFEST -------------------------------------------------------
            self._tick(progress_callback, WINDOWS["finalizing"][0], "finalizing")
            processing_seconds = round(time.monotonic() - started, 2)
            manifest = self._build_manifest(
                project_row,
                language=language,
                voice_id=resolved_voice_id,
                provider=report.get("provider", "piper"),
                sample_rate=final_facts["sample_rate"],
                channels=final_facts["channels"],
                segment_count=len(segment_meta),
                duration_ms=final_facts["duration_ms"],
                script_fingerprint=script_fingerprint,
                generation_fingerprint=generation_fingerprint,
                quality=quality,
                normalization=norm_result,
                warnings=warnings,
                processing_seconds=processing_seconds,
            )
            self._write_json(audio_dir / "narration_manifest.json", manifest)

            summary = {
                "audio_path": "audio/narration.wav",
                "subtitle_path": "subtitles/subtitles.srt",
                "duration_ms": final_facts["duration_ms"],
                "segment_count": len(segment_meta),
                "quality_score": quality["quality_score"],
                "processing_seconds": processing_seconds,
                "warnings": warnings,
            }
            self._tick(progress_callback, 100.0, "finalizing")
            logger.info(
                "Narration complete for project %s: %d ms, %d segments, "
                "QC=%d.",
                project_id, final_facts["duration_ms"], len(segment_meta),
                quality["quality_score"],
            )
            return summary

        except (NarrationError, TTSEngineUnavailableError, TTSVoiceMissingError):
            raise
        except Exception as exc:  # noqa: BLE001 - structural failure
            logger.exception("Narration pipeline failed for project %s.", project_id)
            raise NarrationError(f"Narration generation failed: {exc}") from exc

    # ------------------------------------------------------------------
    def _build_timeline(
        self,
        project_id: str,
        segment_meta: list[dict[str, Any]],
        sample_rate: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Compute exact start/end ms per segment and assembly entries.

        The gap between two consecutive segments is small when they belong
        to the same script section and larger between sections; a short
        lead-in precedes the first segment. Both the timeline and the
        assembly use the SAME gaps, so the assembled WAV length always
        matches the timeline's final end.
        """
        within = self._settings.tts_gap_ms_within_section
        between = self._settings.tts_gap_ms_between_sections
        lead_in = self._settings.tts_lead_in_ms

        timeline_segments: list[dict[str, Any]] = []
        assembly_entries: list[dict[str, Any]] = []
        cursor_ms = 0
        for index, meta in enumerate(segment_meta):
            gap_ms = 0
            if index == 0:
                gap_ms = lead_in
            elif meta["section_index"] == segment_meta[index - 1]["section_index"]:
                gap_ms = within
            else:
                gap_ms = between
            cursor_ms += gap_ms
            start_ms = cursor_ms
            end_ms = cursor_ms + meta["duration_ms"]
            rel_parts = meta["audio_path"].split("/")
            assembly_entries.append({
                "path": self._storage.project_path(project_id, *rel_parts),
                "gap_ms": gap_ms,
            })
            timeline_segments.append({
                "segment_id": meta["segment_id"],
                "sequence": meta["sequence"],
                "text": meta["text"],
                "scene_ids": meta["scene_ids"],
                "section": meta["section"],
                "section_index": meta["section_index"],
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_ms": end_ms - start_ms,
                "audio_path": meta["audio_path"],
            })
            cursor_ms = end_ms

        return {
            "schema_version": 1,
            "sample_rate": sample_rate,
            "channels": self._settings.tts_channels,
            "gap_ms": {
                "within_section": within,
                "between_sections": between,
                "lead_in": lead_in,
            },
            "total_duration_ms": cursor_ms,
            "segments": timeline_segments,
        }, assembly_entries

    # ------------------------------------------------------------------
    def cleanup_artifacts(self, project_id: str) -> None:
        """Remove Phase 6 narration artifacts (never Phase 2/3/4/5 files).

        ``audio/audio.wav`` (the extracted source audio) and everything in
        ``analysis/`` are preserved. Only narration outputs are cleared so
        a retry never mixes old and new segments.
        """
        try:
            root = self._storage.project_root(project_id)
            audio_dir = root / "audio"
            for name in _NARRATION_FILES:
                path = audio_dir / name
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:  # pragma: no cover - best-effort
                    logger.warning("Could not remove %s: %s", path, exc)
            segments_dir = audio_dir / "segments"
            if segments_dir.is_dir():
                shutil.rmtree(segments_dir, ignore_errors=True)
            subtitles_dir = root / "subtitles"
            if subtitles_dir.is_dir():
                shutil.rmtree(subtitles_dir, ignore_errors=True)
            logger.info("Cleared Phase 6 narration artifacts for project %s.", project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Narration artifact cleanup incomplete for %s: %s", project_id, exc,
            )

    # ------------------------------------------------------------------
    def _build_manifest(
        self,
        project_row,
        *,
        language,
        voice_id,
        provider,
        sample_rate,
        channels,
        segment_count,
        duration_ms,
        script_fingerprint,
        generation_fingerprint,
        quality,
        normalization,
        warnings,
        processing_seconds,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {
                "sha256": project_row.get("sha256"),
                "original_filename": project_row.get("original_filename"),
            },
            "generation": {
                "language": language,
                "voice": voice_id,
                "voice_id": voice_id,
                "provider": provider,
                "sample_rate": sample_rate,
                "channels": channels,
                "segment_count": segment_count,
                "duration_ms": duration_ms,
                "script_fingerprint": script_fingerprint,
                "fingerprint": generation_fingerprint,
                "normalization": normalization,
                "gap_ms": {
                    "within_section": self._settings.tts_gap_ms_within_section,
                    "between_sections": self._settings.tts_gap_ms_between_sections,
                    "lead_in": self._settings.tts_lead_in_ms,
                },
            },
            "results": {
                "quality_score": quality["quality_score"],
                "scores": quality["scores"],
                "processing_seconds": processing_seconds,
                "assets": {
                    "segments_dir": "audio/segments",
                    "segments": "audio/segments.json",
                    "timeline": "audio/narration_timeline.json",
                    "audio": "audio/narration.wav",
                    "quality": "audio/narration_quality.json",
                    "subtitles_srt": "subtitles/subtitles.srt",
                    "subtitles_vtt": "subtitles/subtitles.vtt",
                    "manifest": "audio/narration_manifest.json",
                },
            },
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _write_json(path: Path, document: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def _tick(
        callback: Callable[[float, str | None], None] | None,
        progress: float,
        stage: str,
    ) -> None:
        if callback is not None:
            callback(progress, stage)


__all__ = ["NarrationService", "WINDOWS", "_STAGE_LABELS"]
