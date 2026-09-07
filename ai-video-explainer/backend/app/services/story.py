"""Composite Phase 5 job orchestrator: ANALYZED -> SCRIPT_READY.

Runs sequentially inside the existing single worker thread:

    PREPARING_EVIDENCE -> STORY_UNDERSTANDING -> SCENE_IMPORTANCE ->
    SCENE_SELECTION -> DURATION_PLANNING -> SCRIPT_PLANNING ->
    SCRIPT_GENERATION -> QUALITY_CHECK -> FINALIZING

Artifacts (all under ``analysis/story/`` in the project folder; relative
paths only in every document):

    evidence_manifest.json  scene_summaries.json  story.json
    scene_importance.json   selected_scenes.json  duration_plan.json
    script_plan.json        script.json           script_quality.json
    story_manifest.json

Stage policy: every stage is structural - Phase 5 must never fake a story
or a script. If the local LLM is unavailable the job fails with a clear
installation message and the project returns to ANALYZED (Phase 4 results
are preserved). The deterministic QC is the last gate: an empty/garbage
script rejects the run.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.ai.llm import LocalLLMProvider, build_llm_provider
from app.ai.script import ScriptGenerationService
from app.ai.story import StoryUnderstandingService
from app.services.duration_planning import DurationPlanningService
from app.services.evidence import EvidencePreparationService
from app.services.scene_importance import SceneImportanceService
from app.services.script_quality import ScriptQualityService
from app.services.storage import StorageService
from app.utils.errors import (
    EvidenceError,
    ScriptGenerationError,
    StoryError,
)
from app.utils.logging import get_logger

logger = get_logger("app.services.story")

#: Global progress windows (start, end) per stage - honest stage-based.
WINDOWS: dict[str, tuple[float, float]] = {
    "preparing_evidence": (0.0, 10.0),
    "story_understanding": (10.0, 40.0),
    "scene_importance": (40.0, 50.0),
    "scene_selection": (50.0, 60.0),
    "duration_planning": (60.0, 68.0),
    "script_planning": (68.0, 76.0),
    "script_generation": (76.0, 90.0),
    "quality_check": (90.0, 97.0),
    "finalizing": (97.0, 100.0),
}

_STAGE_LABELS = {
    "preparing_evidence": "Preparing evidence",
    "story_understanding": "Understanding story",
    "scene_importance": "Scoring scenes",
    "scene_selection": "Selecting important scenes",
    "duration_planning": "Planning duration",
    "script_planning": "Planning script",
    "script_generation": "Writing explanation",
    "quality_check": "Quality checking",
    "finalizing": "Finalizing",
}


class StoryService:
    """Runs the Phase 5 story+script pipeline for one project (worker thread)."""

    def __init__(
        self,
        settings: Any,
        storage: StorageService,
        provider: LocalLLMProvider | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._provider = provider or build_llm_provider(settings)
        self._evidence = EvidencePreparationService(settings, storage)
        self._story_ai = StoryUnderstandingService(settings, self._provider)
        self._importance = SceneImportanceService(settings)
        self._duration = DurationPlanningService(settings)
        self._script_ai = ScriptGenerationService(settings, self._provider)
        self._quality = ScriptQualityService(settings)

    # ------------------------------------------------------------------
    def run(
        self,
        project_row: dict[str, Any],
        *,
        language: str,
        target_duration_seconds: int,
        progress_callback: Callable[[float, str | None], None] | None = None,
    ) -> dict[str, Any]:
        """Generate story + script for an ANALYZED project; returns the
        summary for SQLite. Raises on any structural failure."""
        project_id = project_row["id"]
        started = time.monotonic()
        warnings: list[str] = []
        dirs = self._storage.ensure_project_dirs(project_id)
        story_dir = dirs["analysis"] / "story"
        story_dir.mkdir(parents=True, exist_ok=True)

        try:
            # ---- EVIDENCE -------------------------------------------------
            self._tick(progress_callback, WINDOWS["preparing_evidence"][0], "preparing_evidence")
            evidence = self._evidence.prepare(project_id, project_row)
            self._write_json(story_dir / "evidence_manifest.json", evidence)

            # ---- STORY UNDERSTANDING --------------------------------------
            self._tick(progress_callback, WINDOWS["story_understanding"][0], "story_understanding")
            story, batch_summaries = self._story_ai.understand(
                evidence,
                progress_callback=self._windowed(progress_callback, "story_understanding"),
            )
            self._write_json(story_dir / "scene_summaries.json", {
                "schema_version": 1,
                "batch_count": len(batch_summaries),
                "batches": batch_summaries,
            })
            self._write_json(story_dir / "story.json", story)
            warnings.extend(story.get("warnings", []))

            # ---- SCENE IMPORTANCE + SELECTION ------------------------------
            self._tick(progress_callback, WINDOWS["scene_importance"][0], "scene_importance")
            importance_doc = self._importance.score(evidence, story)
            self._write_json(story_dir / "scene_importance.json", importance_doc)

            self._tick(progress_callback, WINDOWS["scene_selection"][0], "scene_selection")
            selection_doc = self._importance.select(evidence, story, importance_doc)
            self._write_json(story_dir / "selected_scenes.json", selection_doc)
            warnings.extend(selection_doc.get("warnings", []))

            # ---- DURATION + SCRIPT PLANNING --------------------------------
            self._tick(progress_callback, WINDOWS["duration_planning"][0], "duration_planning")
            duration_plan, script_plan = self._duration.plan(
                evidence, story, selection_doc, target_duration_seconds,
            )
            self._write_json(story_dir / "duration_plan.json", duration_plan)
            warnings.extend(duration_plan.get("warnings", []))

            self._tick(progress_callback, WINDOWS["script_planning"][0], "script_planning")
            self._write_json(story_dir / "script_plan.json", script_plan)

            # ---- SCRIPT GENERATION ------------------------------------------
            self._tick(progress_callback, WINDOWS["script_generation"][0], "script_generation")
            script = self._script_ai.generate_script(
                script_plan, story, evidence, language,
                progress_callback=self._windowed(progress_callback, "script_generation"),
            )
            self._write_json(story_dir / "script.json", script)
            warnings.extend(script.get("warnings", []))

            # ---- QUALITY CHECK -----------------------------------------------
            self._tick(progress_callback, WINDOWS["quality_check"][0], "quality_check")
            quality = self._quality.check(script, duration_plan, story, evidence)
            self._write_json(story_dir / "script_quality.json", quality)
            warnings.extend(quality.get("warnings", []))

            # ---- MANIFEST -----------------------------------------------------
            self._tick(progress_callback, WINDOWS["finalizing"][0], "finalizing")
            processing_seconds = round(time.monotonic() - started, 2)
            manifest = self._build_manifest(
                project_row, story, selection_doc, script, quality,
                language, target_duration_seconds, warnings, processing_seconds,
            )
            self._write_json(story_dir / "story_manifest.json", manifest)

            summary = {
                "content_type": story["content_type"],
                "content_type_confidence": story["content_type_confidence"],
                "selected_scene_count": selection_doc["summary"]["selected_count"],
                "word_count": script["word_count"],
                "quality_score": quality["quality_score"],
                "estimated_duration_seconds": quality["estimated_duration_seconds"],
                "processing_seconds": processing_seconds,
                "warnings": warnings,
            }
            self._tick(progress_callback, 100.0, "finalizing")
            logger.info(
                "Story+script complete for project %s: type=%s, %d words, "
                "quality=%d, scenes=%d.",
                project_id, story["content_type"], script["word_count"],
                quality["quality_score"], selection_doc["summary"]["selected_count"],
            )
            return summary

        except (EvidenceError, StoryError, ScriptGenerationError):
            raise
        except Exception as exc:  # noqa: BLE001 - structural failure
            logger.exception("Story+script pipeline failed for project %s.", project_id)
            raise StoryError(f"Script generation failed: {exc}") from exc

    # ------------------------------------------------------------------
    def cleanup_artifacts(self, project_id: str) -> None:
        """Remove Phase 5 artifacts (``analysis/story/``); Phase 3/4 assets
        are never touched."""
        try:
            root = self._storage.project_root(project_id)
            story_dir = root / "analysis" / "story"
            if not story_dir.is_dir():
                return
            for child in story_dir.iterdir():
                try:
                    if child.is_file():
                        child.unlink()
                except OSError as exc:  # pragma: no cover - best-effort
                    logger.warning("Could not remove %s: %s", child, exc)
            logger.info("Cleared Phase 5 artifacts for project %s.", project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Phase 5 artifact cleanup incomplete for %s: %s", project_id, exc)

    # ------------------------------------------------------------------
    def _build_manifest(
        self, project_row, story, selection_doc, script, quality,
        language, target_duration_seconds, warnings, processing_seconds,
    ) -> dict[str, Any]:
        from app.utils.fingerprints import story_generation_fingerprint

        return {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {
                "sha256": project_row.get("sha256"),
                "original_filename": project_row.get("original_filename"),
                "duration_seconds": project_row.get("duration"),
            },
            "generation": {
                "language": language,
                "target_duration_seconds": target_duration_seconds,
                "fingerprint": story_generation_fingerprint(
                    self._settings, project_row,
                    language=language,
                    target_duration_seconds=target_duration_seconds,
                ),
                "prompt_versions": {
                    "story": self._settings.story_prompt_version,
                    "script": self._settings.script_prompt_version,
                    "planner": self._settings.planner_version,
                },
            },
            "results": {
                "content_type": story["content_type"],
                "content_type_confidence": story["content_type_confidence"],
                "selected_scene_count": selection_doc["summary"]["selected_count"],
                "word_count": script["word_count"],
                "quality_score": quality["quality_score"],
                "estimated_duration_seconds": quality["estimated_duration_seconds"],
                "processing_seconds": processing_seconds,
                "assets": {
                    "evidence_manifest": "analysis/story/evidence_manifest.json",
                    "scene_summaries": "analysis/story/scene_summaries.json",
                    "story": "analysis/story/story.json",
                    "scene_importance": "analysis/story/scene_importance.json",
                    "selected_scenes": "analysis/story/selected_scenes.json",
                    "duration_plan": "analysis/story/duration_plan.json",
                    "script_plan": "analysis/story/script_plan.json",
                    "script": "analysis/story/script.json",
                    "script_quality": "analysis/story/script_quality.json",
                    "manifest": "analysis/story/story_manifest.json",
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

    def _windowed(
        self,
        callback: Callable[[float, str | None], None] | None,
        stage: str,
    ) -> Callable[[float], None] | None:
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


__all__ = ["StoryService", "WINDOWS", "_STAGE_LABELS"]