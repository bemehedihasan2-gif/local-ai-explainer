"""Story understanding (Phase 5): evidence -> structured story model.

Runs a small local LLM (llama.cpp CLI, no cloud) over the compact scene
evidence produced by :class:`EvidencePreparationService`:

1. **Batch summaries** - for long videos the scenes are processed in
   ``STORY_BATCH_SCENES`` chunks (default 15); each batch yields compact
   JSON summaries (events / facts / entities / uncertainties) so the prompt
   size stays bounded regardless of video length.
2. **Global story** - the batch summaries are combined into one structured
   story model (content type, premise, events, turning points, cause/effect,
   ...) with per-claim ``scene_ids`` evidence references.

Anti-hallucination policy (enforced in the prompts *and* in code):

- the model is told to only use the supplied evidence and to move anything
  uncertain into ``uncertain_points`` / ``uncertain`` lists;
- scene ids in the answer are filtered to real ids from the evidence;
- text fields are length-capped;
- an unknown ``content_type`` falls back to ``general``;
- per-scene semantic scores are computed **deterministically in code** from
  which evidence sets a scene appears in - the model never invents scores.

The ``execute()`` contract from Phase 1 is preserved (raises
``NotInPhase1Error`` - this stage runs through the Phase 5 worker instead).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from app.ai.base import PipelineService
from app.ai.llm import LocalLLMProvider, build_llm_provider, extract_json
from app.models.enums import PipelineStage
from app.utils.errors import (
    LLMUnavailableError,
    StoryError,
)
from app.utils.logging import get_logger

logger = get_logger("app.ai.story")

#: Content types the classifier may output (unknown -> "general").
ALLOWED_CONTENT_TYPES = (
    "movie", "short_film", "gameplay", "education", "news", "sports",
    "tutorial", "lecture", "screen_recording", "nature", "animal",
    "social_video", "general",
)

#: Upper bound for every free-text field in the story model.
_MAX_FIELD_CHARS = 220

_BATCH_PROMPT = """You are analyzing one short batch of scenes from a video. \
The scene records below are the ONLY evidence available: transcript excerpts, \
OCR text and numeric visual metadata (brightness/blur/complexity - those are \
not object detections and must never be described as visible objects).

Rules:
- NEVER invent names, places, numbers, dates, dialogue, motives or outcomes \
that the evidence does not support.
- When a detail is uncertain or only hinted at, write it in "uncertain" \
starting with "the video appears to show...".
- scene_ids must be real scene ids from the records.
- Keep every event/fact text under 180 characters.

Respond with ONLY a JSON object (no markdown, no prose) with exactly this schema:
{{
  "events": [{{"text": "what happens", "scene_ids": [3, 4]}}],
  "facts": [{{"text": "a supported factual detail", "scene_ids": [5]}}],
  "entities": ["person/place/thing ONLY if named in the evidence"],
  "uncertain": ["ambiguous detail"]
}}

Scene records:
{records}
"""

_GLOBAL_PROMPT = """You are summarizing the story of a video from compact \
scene-batch summaries. The batch summaries below are the ONLY evidence \
available. A human narrator will later explain this video, so the summary \
must be accurate, chronological and strictly grounded.

Rules:
- NEVER invent names, places, numbers, dates, statistics, dialogue, motives \
or outcomes that the summaries do not support.
- If the content is ambiguous, list it in "uncertain_points" phrased with \
"the video appears to show...".
- scene_ids must be real scene ids from the summaries.
- Keep every free-text field under {max_chars} characters.
- Choose content_type from this list only: {content_types}. If unsure, use \
"general" with a low confidence.

Respond with ONLY a JSON object (no markdown, no prose) with exactly this schema:
{{
  "content_type": "general",
  "content_type_confidence": 0.6,
  "premise": "one or two sentences about the video",
  "main_entities": ["only names present in the evidence"],
  "locations": ["only locations present in the evidence"],
  "chronological_events": [{{"text": "event", "scene_ids": [1, 2]}}],
  "key_turning_points": [{{"text": "turning point", "scene_ids": [5]}}],
  "beginning": [{{"text": "how it starts", "scene_ids": [1]}}],
  "middle": [{{"text": "main development", "scene_ids": [3, 4]}}],
  "ending": [{{"text": "how it ends", "scene_ids": [6]}}],
  "cause_effect": [{{"cause": "cause", "effect": "effect", "scene_ids": [4]}}],
  "important_facts": [{{"text": "fact", "scene_ids": [2]}}],
  "uncertain_points": [],
  "evidence_scene_ids": [1, 2, 3]
}}

Batch summaries:
{summaries}
"""


class StoryUnderstandingService(PipelineService):
    stage = PipelineStage.STORY_UNDERSTANDING
    name = "Story Understanding"
    planned_for = "Phase 4"

    def __init__(self, settings: Any, provider: LocalLLMProvider | None = None) -> None:
        super().__init__(settings)
        self._settings = settings
        self._provider = provider or build_llm_provider(settings)

    # ------------------------------------------------------------------
    def understand(
        self,
        evidence: dict[str, Any],
        progress_callback: Callable[[float], None] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Build the story model from an evidence manifest.

        Returns ``(story, batch_summaries)`` - the batch summaries are kept
        as a separate artifact (``scene_summaries.json``) for auditability.
        """
        if not self._provider.available():
            detail = self._provider.describe()
            raise LLMUnavailableError(
                detail.get("setup_hint") or "The local LLM is not available."
            )

        scenes = evidence["scenes"]
        batch_size = evidence.get("bounds", {}).get(
            "story_batch_scenes", self._settings.story_batch_scenes
        )
        valid_ids = {int(scene["scene_id"]) for scene in scenes}

        batches = [
            scenes[index : index + batch_size]
            for index in range(0, len(scenes), batch_size)
        ]
        batch_summaries: list[dict[str, Any]] = []
        for batch_index, batch in enumerate(batches):
            if progress_callback is not None:
                progress_callback(round((batch_index + 1) / len(batches), 4))
            records = [
                {
                    "scene_id": scene["scene_id"],
                    "start": scene["start"],
                    "end": scene["end"],
                    "transcript": scene.get("transcript_excerpt", ""),
                    "ocr": scene.get("ocr_text", ""),
                    "visual_metadata": scene.get("visual"),
                    "information_density": scene.get("information_density"),
                }
                for scene in batch
            ]
            prompt = _BATCH_PROMPT.format(records=json.dumps(records, ensure_ascii=False))
            raw = self._provider.generate(
                prompt,
                max_tokens=self._settings.llama_max_tokens,
                temperature=self._settings.llm_temperature,
            )
            parsed = extract_json(raw)
            batch_summaries.append(
                self._normalize_batch_summary(parsed, valid_ids)
            )

        if progress_callback is not None:
            progress_callback(1.0)

        prompt = _GLOBAL_PROMPT.format(
            max_chars=_MAX_FIELD_CHARS,
            content_types=", ".join(ALLOWED_CONTENT_TYPES),
            summaries=json.dumps(batch_summaries, ensure_ascii=False, indent=1),
        )
        raw = self._provider.generate(
            prompt,
            max_tokens=self._settings.llama_max_tokens,
            temperature=self._settings.llm_temperature,
        )
        parsed = extract_json(raw)
        story = self._normalize_story(parsed, valid_ids, len(scenes))
        story["schema_version"] = 1
        story["scene_count"] = len(scenes)
        story["scene_scores"] = self._scene_scores(story, valid_ids)
        story["warnings"] = []
        logger.info(
            "Story understood: type=%s (conf=%.2f), events=%d, scenes=%d",
            story["content_type"], story["content_type_confidence"],
            len(story["chronological_events"]), len(scenes),
        )
        return story, batch_summaries

    # ------------------------------------------------------------------
    # Normalization & grounding
    # ------------------------------------------------------------------
    @staticmethod
    def _as_ids(value: Any, valid_ids: set[int]) -> list[int]:
        """Coerce unknown scene_id values to ints; drop anything invalid."""
        result: list[int] = []
        for item in value or []:
            try:
                scene_id = int(item)
            except (TypeError, ValueError):
                continue
            if scene_id in valid_ids and scene_id not in result:
                result.append(scene_id)
        return result

    @classmethod
    def _normalize_batch_summary(
        cls, parsed: dict[str, Any], valid_ids: set[int],
    ) -> dict[str, Any]:
        def events(key: str) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for item in parsed.get(key) or []:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()[:180]
                if not text:
                    continue
                ids = cls._as_ids(item.get("scene_ids"), valid_ids)
                result.append({"text": text, "scene_ids": ids})
            return result

        return {
            "events": events("events"),
            "facts": events("facts"),
            "entities": [
                str(e).strip()[:80] for e in (parsed.get("entities") or [])
                if isinstance(e, str) and e.strip()
            ][:12],
            "uncertain": [
                str(e).strip()[:180] for e in (parsed.get("uncertain") or [])
                if isinstance(e, str) and e.strip()
            ][:12],
        }

    @classmethod
    def _normalize_story(
        cls, parsed: dict[str, Any], valid_ids: set[int], scene_count: int,
    ) -> dict[str, Any]:
        def item_list(key: str) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for entry in parsed.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                text = str(entry.get("text") or "").strip()[:_MAX_FIELD_CHARS]
                if not text:
                    continue
                ids = cls._as_ids(entry.get("scene_ids"), valid_ids)
                result.append({"text": text, "scene_ids": ids})
            return result[:16]

        content_type = str(parsed.get("content_type") or "general").strip().lower()
        unknown_type = content_type not in ALLOWED_CONTENT_TYPES
        if unknown_type:
            content_type = "general"
        try:
            confidence = float(parsed.get("content_type_confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = min(1.0, max(0.0, confidence))
        # An unrecognized content type means the classifier was unsure:
        # fall back to "general" with confidence capped at 0.5.
        if unknown_type:
            confidence = min(confidence, 0.5)

        premise = str(parsed.get("premise") or "").strip()[:_MAX_FIELD_CHARS]
        if not premise:
            premise = (
                "The video's overall subject could not be confidently "
                "determined from the available evidence."
            )

        evidence_ids = cls._as_ids(parsed.get("evidence_scene_ids"), valid_ids)

        return {
            "content_type": content_type,
            "content_type_confidence": round(confidence, 3),
            "premise": premise,
            "main_entities": [
                str(e).strip()[:80] for e in (parsed.get("main_entities") or [])
                if isinstance(e, str) and e.strip()
            ][:12],
            "locations": [
                str(e).strip()[:80] for e in (parsed.get("locations") or [])
                if isinstance(e, str) and e.strip()
            ][:12],
            "chronological_events": item_list("chronological_events"),
            "key_turning_points": item_list("key_turning_points"),
            "beginning": item_list("beginning"),
            "middle": item_list("middle"),
            "ending": item_list("ending"),
            "cause_effect": [
                {
                    "cause": str(entry.get("cause") or "").strip()[:_MAX_FIELD_CHARS],
                    "effect": str(entry.get("effect") or "").strip()[:_MAX_FIELD_CHARS],
                    "scene_ids": cls._as_ids(entry.get("scene_ids"), valid_ids),
                }
                for entry in (parsed.get("cause_effect") or [])
                if isinstance(entry, dict)
                and (entry.get("cause") or entry.get("effect"))
            ][:12],
            "important_facts": item_list("important_facts"),
            "uncertain_points": [
                str(e).strip()[:180] for e in (parsed.get("uncertain_points") or [])
                if isinstance(e, str) and e.strip()
            ][:12],
            "evidence_scene_ids": evidence_ids,
        }

    @staticmethod
    def _scene_scores(story: dict[str, Any], valid_ids: set[int]) -> dict[str, float]:
        """Deterministic semantic importance per scene (0..1).

        Base 0.25 for every scene; evidence membership adds bounded bonuses:
        +0.25 any evidence reference, +0.15 turning point, +0.10
        cause/effect or important fact, +0.10 beginning/ending anchor.
        """
        turning_ids: set[int] = {
            i for item in story["key_turning_points"] for i in item["scene_ids"]
        }
        fact_ids: set[int] = {
            i for item in story["important_facts"] for i in item["scene_ids"]
        }
        cause_ids: set[int] = {
            i for item in story["cause_effect"] for i in item["scene_ids"]
        }
        anchor_ids: set[int] = {
            i for item in story["beginning"] + story["ending"] for i in item["scene_ids"]
        }
        evidence_ids = set(story["evidence_scene_ids"])

        scores: dict[str, float] = {}
        for scene_id in sorted(valid_ids):
            score = 0.25
            if scene_id in evidence_ids:
                score += 0.25
            if scene_id in turning_ids:
                score += 0.15
            if scene_id in fact_ids or scene_id in cause_ids:
                score += 0.10
            if scene_id in anchor_ids:
                score += 0.10
            scores[str(scene_id)] = round(min(1.0, score), 3)
        return scores


__all__ = ["StoryUnderstandingService"]