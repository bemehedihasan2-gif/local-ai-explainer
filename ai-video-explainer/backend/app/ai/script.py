"""Script generation (Phase 5): script plan + evidence -> narration.

The script is written **after** the duration plan exists, so the model
never has to compress a long draft: every section arrives with a scene-id
list, a word budget and compact evidence, and the model writes one
paragraph per section in the user-selected language (English / Hindi /
Bengali).

Originality & grounding rules are part of the prompt AND checked again by
the deterministic QC service afterwards:

- paraphrase and explain; never copy transcript sentences verbatim;
- never invent names, numbers, dates or statistics;
- when evidence is thin, phrase carefully ("the video appears to show...");
- output is natural prose in the target language - the story/planning
  artifacts stay internal and are never mixed into the narration.

The ``execute()`` contract from Phase 1 is preserved (this stage runs
through the Phase 5 worker instead).
"""

from __future__ import annotations

from typing import Any, Callable

from app.ai.base import PipelineService
from app.ai.llm import LocalLLMProvider, build_llm_provider
from app.models.enums import PipelineStage
from app.utils.errors import LLMUnavailableError
from app.utils.logging import get_logger

logger = get_logger("app.ai.script")

_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "bn": "Bengali"}

_STYLE_RULES: dict[str, str] = {
    "movie": "chronological, engaging, clear; explain the important events and their consequences",
    "short_film": "chronological, concise; focus on the few key events and the mood they create",
    "gameplay": "event-focused, energetic but factual; describe player actions and their outcomes",
    "education": "concept-focused, explanatory, structured; introduce terms before using them",
    "news": "factual and neutral; never speculate and never add unsupported claims",
    "sports": "highlight-focused and chronological; describe the key plays and turning points",
    "tutorial": "action-oriented and sequential; explain each step and what it achieves",
    "lecture": "key concepts first, then a logical progression; stay faithful to what was said",
    "screen_recording": "explain the visible actions and their results; mention on-screen text when it is present",
    "nature": "observational; avoid unsupported biological claims",
    "animal": "observational; avoid unsupported behavioral claims",
    "social_video": "chronological, casual but factual; keep the original energy without inventing details",
    "general": "a clear chronological explanation of the main events",
}

_SCRIPT_PROMPT = """You are writing an original video explanation in {language_name}.

The explanation will be narrated aloud, so write natural spoken {language_name} \
- clear sentences, no lists, no headings, no emojis.

Content type: {content_type} (style: {style_rules})
Overall story: {premise}

Rules:
- The section evidence below is the ONLY information you may use. NEVER \
invent names, places, numbers, dates, statistics or outcomes that are not \
in the evidence.
- Paraphrase and explain. NEVER copy sentences from the transcript \
verbatim (a short necessary phrase is acceptable, nothing longer).
- If the evidence for a section is thin, describe it carefully with \
phrases like "the video appears to show...".
- Write in natural {language_name}, not a word-for-word translation.

Write exactly ONE paragraph per section, in the order given, separated by \
a blank line. Do not number the paragraphs and do not write markers like \
[Scene 1]. Target the whole narration at about {total_words} words.

Sections:
{sections}
"""


class ScriptGenerationService(PipelineService):
    stage = PipelineStage.SCRIPT_GENERATION
    name = "Script Generation"
    planned_for = "Phase 5"

    def __init__(self, settings: Any, provider: LocalLLMProvider | None = None) -> None:
        super().__init__(settings)
        self._settings = settings
        self._provider = provider or build_llm_provider(settings)

    # ------------------------------------------------------------------
    def generate_script(
        self,
        script_plan: dict[str, Any],
        story: dict[str, Any],
        evidence: dict[str, Any],
        language: str,
        progress_callback: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        if not self._provider.available():
            detail = self._provider.describe()
            raise LLMUnavailableError(
                detail.get("setup_hint") or "The local LLM is not available."
            )

        if progress_callback is not None:
            progress_callback(0.1)

        language_name = _LANGUAGE_NAMES.get(language, language)
        content_type = story.get("content_type", "general")
        style_rules = _STYLE_RULES.get(content_type, _STYLE_RULES["general"])
        total_words = script_plan.get("total_word_budget", 0)

        sections_text = self._build_sections_prompt(
            script_plan, evidence, language_name,
        )
        prompt = _SCRIPT_PROMPT.format(
            language_name=language_name,
            content_type=content_type,
            style_rules=style_rules,
            premise=(story.get("premise") or "The video's subject was not "
                     "confidently determined.")[:220],
            total_words=total_words,
            sections=sections_text,
        )
        raw = self._provider.generate(
            prompt,
            max_tokens=self._settings.llama_max_tokens,
            temperature=self._settings.llm_temperature,
        )

        if progress_callback is not None:
            progress_callback(0.8)

        paragraphs = [
            paragraph.strip()
            for paragraph in raw.split("\n\n")
            if paragraph.strip()
        ]
        sections = script_plan["sections"]
        warnings: list[str] = []
        section_texts: list[str] = []

        if len(paragraphs) < len(sections):
            warnings.append(
                f"The model returned {len(paragraphs)} paragraph(s) for "
                f"{len(sections)} section(s); short sections were left empty "
                "and flagged by quality control."
            )
            section_texts = paragraphs + [""] * (len(sections) - len(paragraphs))
        else:
            section_texts = paragraphs[: len(sections)]
            if len(paragraphs) > len(sections):
                extras = "\n\n".join(paragraphs[len(sections):])
                section_texts[-1] = (section_texts[-1] + "\n\n" + extras).strip()
                warnings.append(
                    "The model wrote more paragraphs than planned; the extra "
                    "text was appended to the final section."
                )

        built_sections: list[dict[str, Any]] = []
        for section, text in zip(sections, section_texts):
            built_sections.append({
                "scene_ids": section["scene_ids"],
                "purpose": section["purpose"],
                "word_budget": section["word_budget"],
                "text": text,
            })

        full_text = "\n\n".join(text for text in section_texts if text)
        word_count = len(full_text.split()) if full_text else 0
        wpm = self._settings.narration_wpm
        estimated = round(word_count / wpm * 60) if word_count else 0

        if progress_callback is not None:
            progress_callback(1.0)

        logger.info(
            "Script generated: lang=%s, %d words, %d sections.",
            language, word_count, len(built_sections),
        )
        return {
            "schema_version": 1,
            "language": language,
            "language_label": language_name,
            "target_duration_seconds": script_plan.get("target_duration_seconds"),
            "content_type": content_type,
            "content_type_confidence": story.get("content_type_confidence"),
            "sections": built_sections,
            "full_text": full_text,
            "word_count": word_count,
            "estimated_duration_seconds": estimated,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _build_sections_prompt(
        script_plan: dict[str, Any],
        evidence: dict[str, Any],
        language_name: str,
    ) -> str:
        scenes_by_id = {int(s["scene_id"]): s for s in evidence["scenes"]}
        lines: list[str] = []
        for section in script_plan["sections"]:
            budget = section["word_budget"]
            ids = section["scene_ids"]
            label = section["purpose"]
            lines.append(
                f"Section ({label}, ~{budget} words, {language_name}) - "
                f"scenes {ids}:"
            )
            evidence_lines: list[str] = []
            for scene_id in ids[:3]:
                scene = scenes_by_id.get(int(scene_id))
                if not scene:
                    continue
                bits: list[str] = []
                excerpt = scene.get("transcript_excerpt")
                if excerpt:
                    bits.append(f"speech: {excerpt[:160]}")
                ocr_text = scene.get("ocr_text")
                if ocr_text:
                    bits.append(f"on-screen text: {ocr_text[:120]}")
                if not bits and scene.get("visual"):
                    bits.append("(visual metadata only - no readable content)")
                if bits:
                    evidence_lines.append(f"  scene {scene_id}: " + " | ".join(bits))
            if evidence_lines:
                lines.extend(evidence_lines)
            else:
                lines.append("  (no readable evidence for this scene)")
        return "\n".join(lines)


__all__ = ["ScriptGenerationService"]