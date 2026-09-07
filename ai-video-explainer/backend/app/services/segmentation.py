"""Language-aware script segmentation (Phase 6).

Turns the Phase 5 ``script.json`` (paragraphs grouped in sections with
scene references) into narration units: **sentences or short sentence
groups** that become one TTS call and one or more subtitle captions.

Rules:
- a unit never splits a word, an abbreviation (``Mr.``, ``e.g.``) or a
  number (``3.5``), and never splits inside quotes/brackets boundaries;
- English terminates on ``. ! ?``, Hindi/Bengali additionally on the danda
  ``।`` / ``॥`` (the same Unicode codepoints serve both scripts);
- sentence groups are packed up to bounded characters/words so each TTS
  segment stays short enough for readable subtitles on the target machine;
- scene references and section purposes are preserved per segment (Phase 7
  uses them to synchronize narration with the original video).

Pure module: no I/O, fully deterministic and unit-testable.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.logging import get_logger

logger = get_logger("app.services.segmentation")

#: Lowercase tokens after which a ``.`` does NOT end a sentence.
_EN_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc",
    "approx", "inc", "ltd", "co", "fig", "no", "dept", "gen", "hon",
    "rev", "sen", "rep", "ave", "blvd", "rd", "univ", "est", "al",
    "jan", "feb", "mar", "apr",    "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec", "mon", "tue", "wed", "thu", "fri", "sat",
    "sun", "am", "pm", "e.g", "i.e", "a.m", "p.m", "u.s", "u.k",
    "ph.d", "b.c",
}

_TERMINATORS = {".", "!", "?", "\u0964", "\u0965"}  # . ! ? । ॥
_CLOSERS = {")", "]", "}", "\u201d", "\u2019", "\u201c", "\u2018", '"', "'", "\u00bb"}

#: Run of Unicode letters (all scripts) used for abbreviation detection.
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def split_sentences(text: str) -> list[str]:
    """Split ``text`` on real sentence boundaries (never inside a word)."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    sentences: list[str] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        boundary = False
        if char in _TERMINATORS:
            if char == ".":
                # Decimal / ordinal numbers: "3.5" or "1.2." - never split
                # before a digit, and split after a trailing number dot.
                before = text[index - 1] if index > 0 else ""
                after = text[index + 1] if index + 1 < length else ""
                is_decimal = before.isdigit() and after.isdigit()
                is_initial_or_abbrev = _dot_is_abbreviation(text, index)
                ends_sentence = (
                    not before.isdigit()
                    or (after == "" or after.isspace())
                )
                boundary = (
                    not is_decimal
                    and not is_initial_or_abbrev
                    and ends_sentence
                    and (after == "" or after.isspace())
                )
            else:
                after = text[index + 1] if index + 1 < length else ""
                boundary = after == "" or after.isspace() or after in _CLOSERS

        if boundary:
            end = index + 1
            # Attach closing quotes/brackets to the finished sentence.
            while end < length and text[end] in _CLOSERS:
                end += 1
            sentence = text[start:end].strip()
            if sentence:
                sentences.append(sentence)
            while end < length and text[end].isspace():
                end += 1
            index = end
            start = end
            continue
        index += 1

    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _dot_is_abbreviation(text: str, dot_index: int) -> bool:
    """True when the ``.`` at dot_index ends an abbreviation or initial.

    Only letters are inspected: a period after a digit (``3.5``, ``2.``) or
    punctuation is never an abbreviation, so those stay sentence-capable.
    """
    index = dot_index - 1
    while index >= 0 and _LETTER_RE.match(text[index]):
        index -= 1
    token = text[index + 1 : dot_index].lower()
    if not token:
        return False
    if len(token) == 1:
        return True  # single initial: "J. Smith"
    return token in _EN_ABBREVIATIONS


def _word_count(text: str) -> int:
    return len(text.split()) if text.strip() else 0


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Split one over-long unit at whitespace near ``max_chars``."""
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        if current and current_len + len(word) + 1 > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [], 0
        # A single word longer than the limit stays whole (never split).
        current.append(word)
        current_len += len(word) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks or [text]


def _pack_units(
    units: list[str],
    *,
    max_chars: int,
    max_words: int,
) -> list[str]:
    """Pack sentence units into narration segments of bounded size."""
    segments: list[str] = []
    current: list[str] = []
    current_chars = 0
    current_words = 0
    for unit in units:
        unit_chars = len(unit)
        unit_words = _word_count(unit)
        if (
            current
            and current_words + unit_words > max_words
            and len(current) >= 1
        ):
            segments.append(" ".join(current))
            current, current_chars, current_words = [], 0, 0
        elif (
            current
            and current_chars + 1 + unit_chars > max_chars
            and len(current) >= 1
        ):
            segments.append(" ".join(current))
            current, current_chars, current_words = [], 0, 0
        current.append(unit)
        current_chars += unit_chars + (1 if current_chars else 0)
        current_words += unit_words
    if current:
        segments.append(" ".join(current))

    # Units that are themselves larger than the cap are hard-split (their
    # own sentence boundaries may be extremely long).
    expanded: list[str] = []
    for segment in segments:
        if len(segment) <= max_chars:
            expanded.append(segment)
            continue
        expanded.extend(_hard_split(segment, max_chars))
    return expanded


def segment_script(
    script_document: dict[str, Any],
    *,
    max_segment_chars: int = 140,
    max_segment_words: int = 18,
) -> dict[str, Any]:
    """Segment a Phase 5 script document into narration units.

    Returns a segmentation document (schema_version 1) with one entry per
    segment: ``text``, ``scene_ids`` (inherited from the source section),
    ``section`` (purpose), ``section_index`` and a running ``sequence``.
    Never mutates the input document.
    """
    sections = script_document.get("sections") or []
    language = script_document.get("language", "en")
    warnings: list[str] = []
    segments: list[dict[str, Any]] = []
    sections_out: list[dict[str, Any]] = []

    for section_index, section in enumerate(sections):
        raw = section.get("text")
        if not isinstance(raw, str) or not raw.strip():
            warnings.append(
                f"Section {section_index + 1} ('{section.get('purpose', '')}') "
                "has no text and produced no narration segments."
            )
            continue
        units = split_sentences(raw)
        if not units:
            warnings.append(
                f"Section {section_index + 1} text could not be split into "
                "sentences; it was skipped."
            )
            continue
        packed = _pack_units(
            units,
            max_chars=max_segment_chars,
            max_words=max_segment_words,
        )
        scene_ids = [int(sid) for sid in (section.get("scene_ids") or [])]
        section_out = {
            "section_index": section_index,
            "purpose": section.get("purpose", ""),
            "scene_ids": scene_ids,
            "segment_ids": [],
        }
        for packed_text in packed:
            segment_id = len(segments) + 1
            segments.append({
                "segment_id": segment_id,
                "sequence": segment_id,
                "text": packed_text,
                "scene_ids": list(scene_ids),
                "section": section.get("purpose", ""),
                "section_index": section_index,
                "words": _word_count(packed_text),
            })
            section_out["segment_ids"].append(segment_id)
        sections_out.append(section_out)

    if not segments:
        warnings.append(
            "The script produced no narration segments - the script is "
            "empty or unreadable."
        )
    logger.info(
        "Segmented %s section(s) into %d narration unit(s) (lang=%s).",
        len(sections), len(segments), language,
    )
    return {
        "schema_version": 1,
        "language": language,
        "count": len(segments),
        "sections": sections_out,
        "segments": segments,
        "warnings": warnings,
    }


__all__ = ["segment_script", "split_sentences"]
