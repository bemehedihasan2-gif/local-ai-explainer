"""Evidence preparation (Phase 5): Phase 4 artifacts -> compact evidence.

Loads the analysis documents written by Phase 4 and compresses them into a
bounded, per-scene representation suitable for a small local LLM:

- transcript segments are matched to scenes by time overlap and truncated
  to ``STORY_MAX_EXCERPT_CHARS`` per scene (long videos never dump the full
  transcript into a prompt);
- OCR lines are joined and truncated the same way;
- visual values are only reported as *metadata* (brightness / blur /
  complexity) - never interpreted as object/action claims;
- every scene carries ``previous_scene`` / ``next_scene`` ids for
  continuity, the Phase 4 ``information_density`` and a computed
  ``speech_density``.

Only relative paths appear in the manifest (``representative_frame``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.storage import StorageService
from app.utils.errors import EvidenceError
from app.utils.logging import get_logger

logger = get_logger("app.services.evidence")

SCHEMA_VERSION = 1


def _overlaps(seg_start: float, seg_end: float, scene_start: float, scene_end: float) -> bool:
    return seg_start < scene_end and seg_end > scene_start


def _truncate(text: str, limit: int) -> str:
    """Truncate at a word boundary, never mid-word; '' for empty input."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > limit // 2:
        cut = cut[:last_space]
    return cut.rstrip() + "…"


class EvidencePreparationService:
    def __init__(self, settings: Any, storage: StorageService) -> None:
        self._settings = settings
        self._storage = storage

    # ------------------------------------------------------------------
    def prepare(self, project_id: str, project_row: dict[str, Any]) -> dict[str, Any]:
        """Build the evidence manifest; raises :class:`EvidenceError` when a
        required Phase 4 artifact is missing. Returns the manifest dict."""
        metadata_dir = self._storage.project_path(
            project_id, "analysis", "metadata"
        )

        scenes_doc = self._read_json(metadata_dir / "scenes.json", "scenes.json")
        scenes = scenes_doc.get("scenes")
        if not scenes:
            raise EvidenceError(
                "Phase 4 scenes.json is empty or missing - re-run analysis first."
            )
        timeline_doc = self._read_json(metadata_dir / "timeline.json", "timeline.json")
        transcript_doc = self._read_json(
            metadata_dir / "transcript.json", "transcript.json", optional=True
        )
        ocr_doc = self._read_json(metadata_dir / "ocr.json", "ocr.json", optional=True)
        visual_doc = self._read_json(metadata_dir / "visual.json", "visual.json", optional=True)
        manifest_doc = self._read_json(
            metadata_dir / "analysis_manifest.json", "analysis_manifest.json", optional=True
        )

        timeline_by_id = {
            scene["scene_id"]: scene for scene in timeline_doc.get("scenes", [])
        }
        segments = (transcript_doc or {}).get("segments", [])
        ocr_entries = (ocr_doc or {}).get("frames", [])
        visual_by_ts = {
            frame["timestamp"]: frame for frame in (visual_doc or {}).get("frames", [])
        }

        # Per-scene transcript words (for speech density + excerpts).
        words_by_scene: dict[int, int] = {}
        scene_words: list[str] = []
        for scene in scenes:
            scene_id = scene["scene_id"]
            scene_segments = [
                seg for seg in segments
                if _overlaps(
                    float(seg["start"]), float(seg["end"]),
                    float(scene["start"]), float(scene["end"]),
                )
            ]
            words_by_scene[scene_id] = sum(
                len(str(seg.get("text", "")).split()) for seg in scene_segments
            )
            scene_words.append(
                " ".join(str(seg.get("text", "")).strip() for seg in scene_segments)
            )
        max_words = max(words_by_scene.values(), default=0)

        excerpt_limit = self._settings.story_max_excerpt_chars
        compact: list[dict[str, Any]] = []
        for index, scene in enumerate(scenes):
            scene_id = int(scene["scene_id"])
            scene_segments = [
                seg for seg in segments
                if _overlaps(
                    float(seg["start"]), float(seg["end"]),
                    float(scene["start"]), float(scene["end"]),
                )
            ]
            excerpt = " ".join(
                str(seg.get("text", "")).strip() for seg in scene_segments
            )
            scene_ocr = [
                str(entry.get("text", "")).strip()
                for entry in ocr_entries
                if float(scene["start"]) <= float(entry["timestamp"]) <= float(scene["end"])
            ]
            visual = visual_by_ts.get(scene.get("representative_timestamp"))
            timeline_scene = timeline_by_id.get(scene_id, {})

            entry: dict[str, Any] = {
                "scene_id": scene_id,
                "start": round(float(scene["start"]), 3),
                "end": round(float(scene["end"]), 3),
                "duration": round(float(scene["duration"]), 3),
                "previous_scene": scenes[index - 1]["scene_id"] if index > 0 else None,
                "next_scene": scenes[index + 1]["scene_id"] if index < len(scenes) - 1 else None,
                "transcript_excerpt": _truncate(excerpt, excerpt_limit),
                "transcript_words": words_by_scene[scene_id],
                "ocr_text": _truncate(" ".join(scene_ocr), excerpt_limit),
                "visual": {
                    "brightness": round(float(visual["brightness"]), 2),
                    "blur_estimate": round(float(visual["blur_estimate"]), 3),
                    "complexity": round(float(visual["complexity"]), 3),
                } if visual else None,
                "information_density": int(timeline_scene.get("information_density", 0)),
                "speech_density": round(words_by_scene[scene_id] / max_words, 4) if max_words else 0.0,
                "representative_frame": scene.get("representative_frame"),
            }
            compact.append(entry)

        analysis_results = (manifest_doc or {}).get("results", {})
        transcript_available = analysis_results.get("transcript_available")
        if transcript_available is None:
            transcript_available = transcript_doc is not None
        ocr_available = analysis_results.get("ocr_available")
        if ocr_available is None:
            ocr_available = ocr_doc is not None

        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {
                "project_id": project_id,
                "sha256": project_row.get("sha256"),
                "original_filename": project_row.get("original_filename"),
                "duration_seconds": project_row.get("duration"),
                "detected_language": (transcript_doc or {}).get("language")
                or analysis_results.get("detected_language"),
                "analysis_warnings": (manifest_doc or {}).get("warnings", []),
            },
            "bounds": {
                "story_max_excerpt_chars": excerpt_limit,
                "story_batch_scenes": self._settings.story_batch_scenes,
            },
            "summary": {
                "scene_count": len(compact),
                "has_speech": bool(segments),
                "has_ocr": bool(ocr_entries),
                "has_visual": bool((visual_doc or {}).get("frames")),
                "transcript_available": bool(transcript_available),
                "ocr_available": bool(ocr_available),
                "visual_provider": (visual_doc or {}).get("provider"),
                "total_transcript_words": sum(words_by_scene.values()),
                "speech_scenes": sum(1 for e in compact if e["transcript_words"] > 0),
                "ocr_scenes": sum(1 for e in compact if e["ocr_text"]),
                "max_scene_words": max_words,
            },
            "scenes": compact,
        }
        return manifest

    # ------------------------------------------------------------------
    @staticmethod
    def _read_json(path: Path, name: str, *, optional: bool = False) -> dict[str, Any] | None:
        if not path.is_file():
            if optional:
                logger.info("Optional analysis artifact %s missing; continuing.", name)
                return None
            raise EvidenceError(
                f"The analysis artifact '{name}' is missing. Re-run analysis "
                "before generating a script."
            )
        try:
            document = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise EvidenceError(f"Could not read '{name}': {exc}") from exc
        import json

        try:
            parsed = json.loads(document)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"'{name}' is corrupted ({exc}).") from exc
        if not isinstance(parsed, dict):
            raise EvidenceError(f"'{name}' is not a JSON document.")
        return parsed


__all__ = ["EvidencePreparationService"]