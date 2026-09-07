"""Script quality control (Phase 5): deterministic checks + 0-100 score.

Every check is rule-based (no second LLM call, no cloud):

- language validity (script chars match the selected language);
- length against the target word range;
- scene references exist and are chronological;
- empty script -> hard rejection (:class:`ScriptQualityError`);
- repetition (near-duplicate sentences);
- source copying (verbatim n-grams lifted from the transcript);
- unsupported claims (years/percentages/large numbers/superlatives that
  never appear in the evidence corpus);
- safety (control characters, absurd length).

Score formula (documented in ``formula`` in the output and in the README):

    grounding     25%  100 - 40*(unsupported/total sentences) - empty sections
    coverage      20%  100 - 30*(missing story anchors / 3)
    coherence     15%  100 - 50*repetition ratio
    duration_fit  20%  100 - 100*|words - target_mid| / target_mid
    chronology    10%  100 (forward) or 50 (backwards references)
    language       5%  100 or 40
    originality   5%   100 - 100*max verbatim ratio

The estimated narration duration is ``word_count / WPM * 60`` - an
estimate only, real timing arrives with TTS in a later phase.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.errors import ScriptQualityError
from app.utils.logging import get_logger

logger = get_logger("app.services.script_quality")

SCHEMA_VERSION = 1

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_BENGALI_RE = re.compile(r"[\u0980-\u09FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
_BIG_NUMBER_RE = re.compile(r"\b\d{3,}\b")
_SUPERLATIVE_RE = re.compile(
    r"\b(the best|the only|the largest|the biggest|the most famous|"
    r"exactly \d+|first ever|never before)\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।।])\s+")
_TOKEN_RE = re.compile(r"[^0-9a-z]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.split(text.lower())) - {""}


class ScriptQualityService:
    def __init__(self, settings: Any) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    def check(
        self,
        script: dict[str, Any],
        duration_plan: dict[str, Any],
        story: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        language = script["language"]
        word_count = int(script.get("word_count", 0))
        full_text = script.get("full_text", "")
        sections = script.get("sections", [])

        if not full_text or word_count == 0:
            raise ScriptQualityError(
                "The generated script is empty - nothing can be narrated. "
                "Try regenerating; if it persists, check the local model "
                "output (LLAMA_MODEL_PATH)."
            )
        if word_count > self._settings.script_word_targets[240][1] * 2 + 200:
            raise ScriptQualityError(
                f"The script is far too long ({word_count} words) - likely "
                "the model repeated itself. Regenerate with a tighter "
                "max-token setting (LLAMA_MAX_TOKENS)."
            )
        if any(ord(char) < 32 and char not in "\n\t" for char in full_text):
            raise ScriptQualityError(
                "The script contains control characters and cannot be narrated."
            )

        warnings: list[str] = list(script.get("warnings", []))
        checks: list[dict[str, Any]] = []

        selected_ids = [int(row["scene_id"]) for row in duration_plan["scenes"]]
        real_ids = {int(scene["scene_id"]) for scene in evidence["scenes"]}
        section_ids = [int(sid) for section in sections for sid in section["scene_ids"]]

        # ---- evidence grounding: scene validity + empty sections -------
        invalid_refs = [sid for sid in section_ids if sid not in selected_ids]
        empty_sections = sum(1 for section in sections if not (section.get("text") or "").strip())
        grounding = 100.0
        if invalid_refs:
            grounding -= 100.0
            warnings.append(
                f"Script references scenes {invalid_refs} that are not in the "
                "selected scene set."
            )
        grounding -= 15.0 * empty_sections
        if empty_sections:
            warnings.append(
                f"{empty_sections} section(s) have no narration text."
            )
        grounding = max(0.0, grounding)

        # ---- unsupported claims (only when evidence lacks the token) ----
        corpus = _tokens(
            " ".join(
                scene.get("transcript_excerpt", "") + " " + scene.get("ocr_text", "")
                for scene in evidence["scenes"]
            )
        )
        sentences = [
            sentence.strip() for sentence in _SENTENCE_SPLIT_RE.split(full_text)
            if sentence.strip()
        ]
        unsupported: list[str] = []
        for sentence in sentences:
            lowered = sentence.lower()
            suspects: list[str] = []
            suspects.extend(_YEAR_RE.findall(sentence))
            suspects.extend(_PERCENT_RE.findall(sentence))
            suspects.extend(_BIG_NUMBER_RE.findall(sentence))
            if _SUPERLATIVE_RE.search(lowered):
                suspects.append(_SUPERLATIVE_RE.search(lowered).group(0))
            for suspect in suspects:
                suspect_tokens = _tokens(suspect)
                if suspect_tokens and not (suspect_tokens & corpus):
                    unsupported.append(f"{suspect!r} in: {sentence[:120]}")
                    break
        if unsupported:
            warnings.append(
                f"{len(unsupported)} sentence(s) contain numbers/claims not "
                f"found in the evidence: {unsupported[0][:140]}"
            )
        unsupported_ratio = len(unsupported) / max(1, len(sentences))
        grounding -= 40.0 * unsupported_ratio
        grounding = max(0.0, grounding)

        # ---- coverage: story anchors (beginning/middle/ending) ---------
        anchor_lists = [story.get("beginning", []), story.get("middle", []), story.get("ending", [])]
        missing_anchors = sum(
            1 for items in anchor_lists
            if items and not any(
                sid in selected_ids for item in items for sid in item["scene_ids"]
            )
        )
        coverage = max(0.0, 100.0 - 30.0 * missing_anchors)
        if missing_anchors:
            warnings.append(
                f"{missing_anchors} story anchor section(s) (beginning/middle/"
                "ending) are not covered by selected scenes."
            )

        # ---- repetition (near-duplicate sentences) ----------------------
        repetition_ratio = 0.0
        if len(sentences) > 1:
            pairs = 0
            for index, left in enumerate(sentences):
                left_tokens = _tokens(left)
                for right in sentences[index + 1:]:
                    union = left_tokens | _tokens(right)
                    if union and len(left_tokens & _tokens(right)) / len(union) > 0.8:
                        pairs += 1
            repetition_ratio = pairs / len(sentences)
            if pairs:
                warnings.append(
                    f"{pairs} near-duplicate sentence pair(s) detected."
                )
        coherence = max(0.0, 100.0 - 50.0 * repetition_ratio)

        # ---- source copying (verbatim transcript n-grams) ---------------
        max_verbatim_run = self._max_verbatim_run(full_text, evidence)
        verbatim_ratio = max_verbatim_run / max(1, word_count)
        originality = max(0.0, 100.0 - 100.0 * verbatim_ratio)
        if max_verbatim_run >= 8:
            warnings.append(
                f"A {max_verbatim_run}-word passage appears verbatim in the "
                "transcript; the narration should paraphrase."
            )
        elif max_verbatim_run >= 5:
            warnings.append(
                "The narration repeats several transcript phrases; consider "
                "paraphrasing more aggressively."
            )

        # ---- chronology ---------------------------------------------------
        flat_ids = section_ids
        backwards = any(
            left > right for left, right in zip(flat_ids, flat_ids[1:])
        )
        chronology = 50.0 if backwards else 100.0
        if backwards:
            warnings.append(
                "Scene references are not in chronological order."
            )

        # ---- language ------------------------------------------------------
        language_ok, language_message = self._language_check(language, full_text)
        language_score = 100.0 if language_ok else 40.0
        if not language_ok:
            warnings.append(language_message)

        # ---- duration fit ---------------------------------------------------
        word_min = int(duration_plan["target_word_min"])
        word_max = int(duration_plan["target_word_max"])
        word_mid = (word_min + word_max) // 2
        duration_fit = max(
            0.0, 100.0 - 100.0 * abs(word_count - word_mid) / max(1, word_mid)
        )
        wpm = self._settings.narration_wpm
        estimated_seconds = round(word_count / wpm * 60)
        if not (word_min <= word_count <= word_max):
            warnings.append(
                f"Word count {word_count} is outside the target range "
                f"[{word_min}, {word_max}] for this duration."
            )

        scores = {
            "grounding_score": round(grounding, 1),
            "coverage_score": round(coverage, 1),
            "coherence_score": round(coherence, 1),
            "duration_fit_score": round(duration_fit, 1),
            "chronology_score": round(chronology, 1),
            "language_score": round(language_score, 1),
            "originality_score": round(originality, 1),
        }
        weights = {
            "grounding": 0.25, "coverage": 0.20, "coherence": 0.15,
            "duration_fit": 0.20, "chronology": 0.10, "language": 0.05,
            "originality": 0.05,
        }
        overall = round(
            scores["grounding_score"] * weights["grounding"]
            + scores["coverage_score"] * weights["coverage"]
            + scores["coherence_score"] * weights["coherence"]
            + scores["duration_fit_score"] * weights["duration_fit"]
            + scores["chronology_score"] * weights["chronology"]
            + scores["language_score"] * weights["language"]
            + scores["originality_score"] * weights["originality"]
        )

        checks = [
            {"check": "language", "passed": language_ok,
             "severity": "ok" if language_ok else "warn", "message": language_message},
            {"check": "length", "passed": word_min <= word_count <= word_max,
             "severity": "ok" if word_min <= word_count <= word_max else "warn",
             "message": f"{word_count} words (target {word_min}-{word_max})"},
            {"check": "scene_validity", "passed": not invalid_refs,
             "severity": "ok" if not invalid_refs else "error",
             "message": "all referenced scenes are selected" if not invalid_refs
             else f"invalid scene references: {invalid_refs}"},
            {"check": "chronology", "passed": not backwards,
             "severity": "ok" if not backwards else "warn",
             "message": "scene references move forward in time" if not backwards
             else "scene references are out of order"},
            {"check": "repetition", "passed": repetition_ratio < 0.1,
             "severity": "ok" if repetition_ratio < 0.1 else "warn",
             "message": f"{repetition_ratio:.0%} near-duplicate sentence pairs"},
            {"check": "source_copying", "passed": max_verbatim_run < 8,
             "severity": "ok" if max_verbatim_run < 8 else "warn",
             "message": f"max verbatim transcript run: {max_verbatim_run} words"},
            {"check": "unsupported_claims", "passed": not unsupported,
             "severity": "ok" if not unsupported else "warn",
             "message": f"{len(unsupported)} ungrounded claim(s) flagged"},
            {"check": "empty_script", "passed": True, "severity": "ok",
             "message": "script has narration text"},
        ]

        return {
            "schema_version": SCHEMA_VERSION,
            "quality_score": overall,
            "scores": scores,
            "formula": weights,
            "checks": checks,
            "warnings": warnings,
            "word_count": word_count,
            "target_word_min": word_min,
            "target_word_max": word_max,
            "estimated_duration_seconds": estimated_seconds,
            "narration_wpm": wpm,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _language_check(language: str, text: str) -> tuple[bool, str]:
        chars = [char for char in text if not char.isspace()]
        devanagari = len(_DEVANAGARI_RE.findall(text))
        bengali = len(_BENGALI_RE.findall(text))
        latin = len(_LATIN_RE.findall(text))
        if language == "hi":
            ok = devanagari > 0 and devanagari >= latin * 0.15
            message = (
                "Script contains Devanagari text (Hindi)" if ok
                else "The narration is not in Hindi - no Devanagari text found."
            )
            return ok, message
        if language == "bn":
            ok = bengali > 0 and bengali >= latin * 0.15
            message = (
                "Script contains Bengali script text" if ok
                else "The narration is not in Bengali - no Bengali text found."
            )
            return ok, message
        # English: should not be dominated by Indic scripts.
        ok = devanagari + bengali < max(1, len(chars)) * 0.5
        message = (
            "Script is written in Latin script (English)" if ok
            else "The narration does not look like English - an Indic script "
            "dominates the text."
        )
        return ok, message

    @staticmethod
    def _max_verbatim_run(full_text: str, evidence: dict[str, Any]) -> int:
        """Longest run of script words that appear consecutively in the
        transcript (contiguous 5-gram matches extended to full runs)."""
        transcript_text = " ".join(
            scene.get("transcript_excerpt", "") for scene in evidence["scenes"]
        )
        transcript_tokens = [t for t in _TOKEN_RE.split(transcript_text.lower()) if t]
        script_tokens = [t for t in _TOKEN_RE.split(full_text.lower()) if t]
        if len(transcript_tokens) < 5 or len(script_tokens) < 5:
            return 0
        transcript_5grams = {
            tuple(transcript_tokens[index:index + 5])
            for index in range(len(transcript_tokens) - 4)
        }
        # Positions in the script whose 5-gram also appears in the transcript.
        matches = [
            index for index in range(len(script_tokens) - 4)
            if tuple(script_tokens[index:index + 5]) in transcript_5grams
        ]
        max_run = 0
        run = 0
        for index, position in enumerate(matches):
            run = run + 1 if index > 0 and position == matches[index - 1] + 1 else 1
            max_run = max(max_run, run + 4)
        return max_run


__all__ = ["ScriptQualityService"]