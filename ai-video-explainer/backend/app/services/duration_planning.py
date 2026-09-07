"""Duration planning & script planning (Phase 5).

The narration is planned **before** any prose is written: the target
duration defines a word budget (2 min: 250-300, 3 min: 375-450, 4 min:
500-600 by default), that budget is allocated across the important scenes
by evidence weight, and a section plan (hook -> sections -> ending) is
built so the LLM never has to compress a long draft afterwards.

Allocation rules:

- a scene with no meaningful evidence (no speech, no OCR, no visual
  metadata) receives a 0-word budget - it may still be selected for
  continuity but is never narrated;
- when *no* scene has evidence (e.g. a fully silent, textless video) the
  budget falls back to duration-proportional allocation so a script is
  still produced from the story understanding;
- per-scene budgets are clamped to ``MIN_WORDS_PER_SCENE`` /
  ``MAX_WORDS_PER_SCENE`` and then scaled/trimmed so the total lands inside
  the target range (natural narration beats hitting an exact number).
"""

from __future__ import annotations

from typing import Any

from app.utils.logging import get_logger

logger = get_logger("app.services.duration_planning")

SCHEMA_VERSION = 1

_PURPOSES = {
    "introduction": "introduction",
    "turning point": "turning point",
    "conclusion": "conclusion",
    "key information": "key information",
    "development": "development",
}


class DurationPlanningService:
    def __init__(self, settings: Any) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    def plan(
        self,
        evidence: dict[str, Any],
        story: dict[str, Any],
        selection: dict[str, Any],
        target_duration_seconds: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return (duration_plan, script_plan) for the selected scenes."""
        targets = self._settings.script_word_targets.get(target_duration_seconds)
        if targets is None:
            targets = self._settings.script_word_targets[180]
        word_min, word_max = int(targets[0]), int(targets[1])
        word_mid = (word_min + word_max) // 2
        wpm = self._settings.narration_wpm
        warnings: list[str] = []

        scenes_by_id = {int(s["scene_id"]): s for s in evidence["scenes"]}
        selected = selection["selected"]  # already ordered by timeline

        # Trim: if even the minimum budget per scene exceeds the target,
        # drop the least important non-anchor scenes first.
        min_words = self._settings.min_words_per_scene
        max_words = self._settings.max_words_per_scene
        while (
            len(selected) > self._settings.min_scenes_per_script
            and len(selected) * min_words > word_max
        ):
            droppable = [
                row for row in selected[1:-1]
            ]
            if not droppable:
                break
            weakest = min(droppable, key=lambda row: row["importance_score"])
            selected = [row for row in selected if row["scene_id"] != weakest["scene_id"]]

        # Evidence weight per selected scene (0 for evidence-less scenes).
        weights: list[float] = []
        for row in selected:
            scene = scenes_by_id[int(row["scene_id"])]
            has_evidence = bool(
                scene.get("transcript_excerpt") or scene.get("ocr_text") or scene.get("visual")
            )
            weights.append(row["importance_score"] if has_evidence else 0.0)

        if sum(weights) <= 0:
            # Silent/textless video: fall back to duration-proportional
            # allocation so the narrator still has something to say.
            weights = [row["duration"] for row in selected]
            warnings.append(
                "No selected scene carries speech/OCR/visual evidence; the "
                "narration budget was spread by scene duration instead."
            )

        total_weight = sum(weights)
        budgets = [
            round(word_mid * weight / total_weight) if total_weight else 0
            for weight in weights
        ]
        budgets = [
            min(max_words, max(0, budget)) for budget in budgets
        ]
        # Zero-weight scenes stay at 0; clamp the rest to the minimum.
        budgets = [
            max(min_words, budget) if weight > 0 else 0
            for weight, budget in zip(weights, budgets)
        ]

        # Fit the total inside [word_min, word_max] (deterministic, bounded).
        budgets = self._fit_budget(budgets, weights, word_min, word_max, min_words, max_words)
        total = sum(budgets)
        if not (word_min <= total <= word_max):
            warnings.append(
                f"Final budget ({total} words) is outside the target range "
                f"[{word_min}, {word_max}] - narration may run slightly "
                "short/long of the selected duration."
            )

        duration_rows = []
        for row, budget in zip(selected, budgets):
            scene = scenes_by_id[int(row["scene_id"])]
            duration_rows.append({
                "scene_id": int(row["scene_id"]),
                "start": row["start"],
                "end": row["end"],
                "importance_score": row["importance_score"],
                "reasons": row["reasons"],
                "word_budget": budget,
                "evidence": (
                    "speech" if scene.get("transcript_excerpt")
                    else "ocr" if scene.get("ocr_text")
                    else "visual" if scene.get("visual")
                    else "none"
                ),
                "selected": True,
            })
        estimated = round(total / wpm * 60)

        duration_plan: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "target_duration_seconds": target_duration_seconds,
            "target_word_min": word_min,
            "target_word_max": word_max,
            "target_word_mid": word_mid,
            "narration_wpm": wpm,
            "estimated_duration_seconds": estimated,
            "scenes": duration_rows,
            "total_word_budget": total,
            "warnings": warnings,
        }

        script_plan = self._build_script_plan(
            selected, budgets, story, scenes_by_id, target_duration_seconds,
        )
        return duration_plan, script_plan

    # ------------------------------------------------------------------
    def _build_script_plan(
        self,
        selected: list[dict[str, Any]],
        budgets: list[int],
        story: dict[str, Any],
        scenes_by_id: dict[int, dict[str, Any]],
        target_duration_seconds: int,
    ) -> dict[str, Any]:
        """Group selected scenes into narrative sections by story role."""
        turning_ids = {
            i for item in story.get("key_turning_points", []) for i in item["scene_ids"]
        }
        beginning_ids = {
            i for item in story.get("beginning", []) for i in item["scene_ids"]
        }
        ending_ids = {
            i for item in story.get("ending", []) for i in item["scene_ids"]
        }
        fact_ids = {
            i for item in story.get("important_facts", []) + story.get("cause_effect", [])
            for i in item["scene_ids"]
        }

        def purpose(scene_id: int) -> str:
            if scene_id in beginning_ids:
                return "introduction"
            if scene_id in turning_ids:
                return "turning point"
            if scene_id in ending_ids:
                return "conclusion"
            if scene_id in fact_ids:
                return "key information"
            return "development"

        sections: list[dict[str, Any]] = []
        for row, budget in zip(selected, budgets):
            scene_id = int(row["scene_id"])
            role = purpose(scene_id)
            if sections and sections[-1]["purpose"] == role:
                sections[-1]["scene_ids"].append(scene_id)
                sections[-1]["word_budget"] += budget
            else:
                sections.append({
                    "scene_ids": [scene_id],
                    "purpose": role,
                    "word_budget": budget,
                })

        hook = (story.get("premise") or "").strip()
        if len(hook) > 160:
            hook = hook[:157].rstrip() + "…"

        ending_section = sections[-1] if sections else {
            "scene_ids": [], "purpose": "conclusion", "word_budget": 0,
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "target_duration_seconds": target_duration_seconds,
            "hook": hook,
            "sections": sections,
            "ending": ending_section,
            "total_word_budget": sum(section["word_budget"] for section in sections),
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _fit_budget(
        budgets: list[int], weights: list[float],
        word_min: int, word_max: int, min_words: int, max_words: int,
    ) -> list[int]:
        """Scale/trim the allocation into [word_min, word_max] (bounded)."""
        total = sum(budgets)
        if word_min <= total <= word_max:
            return budgets
        budgets = list(budgets)

        if total > word_max:
            # Trim proportionally, respecting the per-scene minimum.
            excess = total - word_max
            for _ in range(100):
                if excess <= 0:
                    break
                candidates = [
                    i for i, budget in enumerate(budgets)
                    if budget > min_words and weights[i] > 0
                ]
                if not candidates:
                    break
                heaviest = max(candidates, key=lambda i: budgets[i])
                cut = min(excess, budgets[heaviest] - min_words)
                budgets[heaviest] -= cut
                excess -= cut
        else:
            # Top up the highest-weight scenes first, respecting the cap.
            deficit = word_min - total
            for _ in range(100):
                if deficit <= 0:
                    break
                candidates = [
                    i for i, budget in enumerate(budgets)
                    if budget < max_words and weights[i] > 0
                ]
                if not candidates:
                    break
                heaviest = max(candidates, key=lambda i: weights[i])
                add = min(deficit, max_words - budgets[heaviest])
                budgets[heaviest] += add
                deficit -= add
        return budgets


__all__ = ["DurationPlanningService", "SCHEMA_VERSION"]