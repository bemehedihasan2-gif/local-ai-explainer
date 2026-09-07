"""Scene importance & selection (Phase 5).

Deterministic weighted scoring of every scene from the evidence manifest +
the story model, then a coverage-aware selection that preserves narrative
flow (beginning -> development -> ending) instead of only picking the
highest numbers.

Weights (defaults in ``config.py``, re-normalized here):

    information_density  25%
    speech_density       15%
    semantic (story)     25%
    turning_point        15%
    continuity           10%
    OCR context           5%
    ------------------------
    additive total       95%  (re-normalized to 100%)
    redundancy adjustment 5%  (multiplicative penalty, capped)

Selection rules:

- the first and last scenes are always kept (story anchors);
- candidates are picked greedily by score, then gaps between selected
  scenes are filled so the timeline stays covered (``max_selected_scenes``
  cap, configurable);
- near-duplicate *consecutive* scenes (text similarity > 0.85) lose the
  lower-scored one unless it is a story anchor - visually similar but
  story-critical scenes are always kept;
- every selected scene carries human-readable ``reasons`` derived from the
  components that actually drove its score.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.logging import get_logger

logger = get_logger("app.services.scene_importance")

SCHEMA_VERSION = 1

#: Consecutive selected scenes with higher text similarity get redundancy-removed.
_REDUNDANCY_SIMILARITY = 0.85

_TOKEN_RE = re.compile(r"[^0-9a-z]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.split(text.lower())) - {""}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union)


class SceneImportanceService:
    def __init__(self, settings: Any) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    def weights(self) -> dict[str, float]:
        settings = self._settings
        return {
            "information_density": settings.importance_weight_information_density,
            "speech_density": settings.importance_weight_speech_density,
            "semantic": settings.importance_weight_semantic,
            "turning_point": settings.importance_weight_turning_point,
            "continuity": settings.importance_weight_continuity,
            "ocr": settings.importance_weight_ocr,
            "redundancy_adjustment": settings.importance_redundancy_penalty,
        }

    # ------------------------------------------------------------------
    def score(
        self, evidence: dict[str, Any], story: dict[str, Any],
    ) -> dict[str, Any]:
        """Per-scene importance scores with components and reasons."""
        scenes = evidence["scenes"]
        count = len(scenes)
        weights = self.weights()
        additive_total = (
            weights["information_density"] + weights["speech_density"]
            + weights["semantic"] + weights["turning_point"]
            + weights["continuity"] + weights["ocr"]
        )
        story_scores = story.get("scene_scores", {})
        turning_ids = {
            int(item["scene_ids"][0]) if item["scene_ids"] else -1
            for item in story.get("key_turning_points", [])
        }

        # Continuity anchors at scene-index fractions 0, 1/3, 2/3, end.
        span = max(1.0, (count - 1) / 3.0)
        anchor_positions = [0.0, (count - 1) / 3.0, 2 * (count - 1) / 3.0, float(count - 1)]

        scored: list[dict[str, Any]] = []
        for index, scene in enumerate(scenes):
            scene_id = int(scene["scene_id"])
            info = float(scene.get("information_density", 0)) / 100.0
            speech = float(scene.get("speech_density", 0.0))
            semantic = float(story_scores.get(str(scene_id), 0.25))
            turning = 1.0 if scene_id in turning_ids else 0.0
            ocr = 1.0 if scene.get("ocr_text") else 0.0
            continuity = max(
                min(1.0, 1.0 - abs(index - anchor) / span) for anchor in anchor_positions
            )

            additive = (
                weights["information_density"] * info
                + weights["speech_density"] * speech
                + weights["semantic"] * semantic
                + weights["turning_point"] * turning
                + weights["continuity"] * continuity
                + weights["ocr"] * ocr
            )
            normalized = min(1.0, additive / max(1e-9, additive_total))

            similarity = self._max_neighbor_similarity(index, scenes)
            penalty = weights["redundancy_adjustment"] * similarity
            final_score = round(normalized * (1.0 - penalty), 4)

            reasons = self._reasons(
                info=info, speech=speech, semantic=semantic, turning=turning,
                continuity=continuity, ocr=ocr,
                is_first=index == 0, is_last=index == count - 1,
            )
            scored.append({
                "scene_id": scene_id,
                "index": index,
                "importance_score": final_score,
                "components": {
                    "information_density": round(info, 4),
                    "speech_density": round(speech, 4),
                    "semantic": round(semantic, 4),
                    "turning_point": round(turning, 4),
                    "continuity": round(continuity, 4),
                    "ocr": round(ocr, 4),
                    "redundancy_penalty": round(penalty, 4),
                },
                "reasons": reasons,
            })

        return {
            "schema_version": SCHEMA_VERSION,
            "weights": weights,
            "weight_notes": (
                "The six additive weights are re-normalized to 100%; the "
                "redundancy adjustment applies a multiplicative penalty of "
                "up to 5% for text-duplicate neighbors."
            ),
            "summary": {
                "scene_count": count,
                "scored": len(scored),
            },
            "scenes": scored,
        }

    # ------------------------------------------------------------------
    def select(
        self,
        evidence: dict[str, Any],
        story: dict[str, Any],
        importance: dict[str, Any],
    ) -> dict[str, Any]:
        """Pick the scenes the narration will cover (coverage-first)."""
        scenes = evidence["scenes"]
        count = len(scenes)
        by_id = {int(item["scene_id"]): item for item in importance["scenes"]}
        warnings: list[str] = []

        max_selected = min(
            self._settings.max_selected_scenes, count
        )
        if count == 1:
            selected_idx = [0]
        else:
            selected_idx = self._greedy_with_coverage(
                by_id, count, max_selected,
            )
            selected_idx = self._remove_redundant_pairs(
                selected_idx, scenes, by_id,
            )

        if len(selected_idx) < self._settings.min_scenes_per_script and count > 1:
            warnings.append(
                f"Only {len(selected_idx)} scenes were selected (video has "
                f"{count}); the script will be shorter than ideal."
            )

        ranked = {index: rank for rank, index in enumerate(selected_idx, start=1)}
        result: list[dict[str, Any]] = []
        for index, scene in enumerate(scenes):
            item = by_id[int(scene["scene_id"])]
            item = dict(item)
            item["selected"] = index in ranked
            item["selected_rank"] = ranked.get(index)
            result.append(item)

        selected_rows = []
        for index in selected_idx:
            scene = scenes[index]
            item = by_id[int(scene["scene_id"])]
            selected_rows.append({
                "scene_id": int(scene["scene_id"]),
                "start": scene["start"],
                "end": scene["end"],
                "duration": scene["duration"],
                "importance_score": item["importance_score"],
                "reasons": item["reasons"],
                "representative_frame": scene.get("representative_frame"),
            })

        logger.info(
            "Selected %d/%d scenes for narration.", len(selected_idx), count,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "summary": {
                "scene_count": count,
                "selected_count": len(selected_idx),
                "selected_ids": [row["scene_id"] for row in selected_rows],
                "max_selected_scenes": max_selected,
            },
            "scenes": result,
            "selected": selected_rows,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    def _greedy_with_coverage(
        self, by_id: dict[int, dict[str, Any]], count: int, max_selected: int,
    ) -> list[int]:
        """Always keep first/last, pick by score, then fill the largest gap."""
        selected = {0, count - 1}
        ordered = sorted(by_id.values(), key=lambda item: -item["importance_score"])
        for item in ordered:
            if len(selected) >= max_selected:
                break
            index = int(item["index"])
            if index not in selected:
                selected.add(index)

        # Fill gaps so no two consecutive selected scenes are too far apart.
        max_gap = max(1, (count + max_selected - 1) // max(1, max_selected))
        for _ in range(count * 2):  # bounded loop
            if len(selected) >= max_selected:
                break
            gap_start, gap_end, gap_size = self._largest_gap(sorted(selected), count)
            if gap_size <= max_gap:
                break
            candidates = [
                by_id[int(scenes_idx)] for scenes_idx in range(gap_start + 1, gap_end)
            ]
            if not candidates:
                break
            best = max(candidates, key=lambda item: item["importance_score"])
            selected.add(int(best["index"]))
        return sorted(selected)

    @staticmethod
    def _largest_gap(selected: list[int], count: int) -> tuple[int, int, int]:
        best = (selected[0], selected[-1], 0)
        for left, right in zip(selected, selected[1:]):
            if right - left > best[2]:
                best = (left, right, right - left)
        return best

    def _remove_redundant_pairs(
        self, selected_idx: list[int], scenes: list[dict[str, Any]], by_id: dict[int, dict[str, Any]],
    ) -> list[int]:
        """Drop lower-scored scene from near-duplicate consecutive pairs
        (never the first/last story anchors)."""
        if len(selected_idx) < 3:
            return selected_idx
        removed: set[int] = set()
        for left, right in zip(selected_idx, selected_idx[1:]):
            if left in removed or right in removed:
                continue
            if left == 0 or right == len(scenes) - 1:
                continue
            left_text = (
                scenes[left].get("transcript_excerpt", "")
                + " " + scenes[left].get("ocr_text", "")
            )
            right_text = (
                scenes[right].get("transcript_excerpt", "")
                + " " + scenes[right].get("ocr_text", "")
            )
            similarity = _jaccard(_tokens(left_text), _tokens(right_text))
            if similarity < _REDUNDANCY_SIMILARITY:
                continue
            left_score = by_id[scenes[left]["scene_id"]]["importance_score"]
            right_score = by_id[scenes[right]["scene_id"]]["importance_score"]
            removed.add(left if left_score <= right_score else right)
        return [index for index in selected_idx if index not in removed]

    @staticmethod
    def _max_neighbor_similarity(index: int, scenes: list[dict[str, Any]]) -> float:
        window = range(max(0, index - 2), min(len(scenes), index + 3))
        own = _tokens(
            scenes[index].get("transcript_excerpt", "") + " " + scenes[index].get("ocr_text", "")
        )
        best = 0.0
        for other in window:
            if other == index:
                continue
            other_tokens = _tokens(
                scenes[other].get("transcript_excerpt", "") + " " + scenes[other].get("ocr_text", "")
            )
            best = max(best, _jaccard(own, other_tokens))
        return best

    @staticmethod
    def _reasons(
        *, info: float, speech: float, semantic: float, turning: float,
        continuity: float, ocr: float, is_first: bool, is_last: bool,
    ) -> list[str]:
        reasons: list[str] = []
        if turning >= 1.0:
            reasons.append("turning point")
        if semantic >= 0.6:
            reasons.append("key story event")
        if info >= 0.6:
            reasons.append("high information density")
        if speech >= 0.5:
            reasons.append("speech-heavy")
        if is_first:
            reasons.append("opening scene")
        if is_last:
            reasons.append("closing scene")
        if continuity >= 0.95 and not reasons:
            reasons.append("continuity anchor")
        if ocr >= 1.0 and info < 0.4 and not reasons:
            reasons.append("on-screen text")
        if not reasons:
            reasons.append("timeline coverage")
        return reasons[:3]


__all__ = ["SceneImportanceService"]