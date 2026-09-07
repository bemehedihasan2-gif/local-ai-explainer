"""Subtitle generation from the *measured* narration timeline (Phase 6).

Subtitle timing is derived exclusively from the actual synthesized audio
segment durations (``audio/narration_timeline.json``) - never from word
counts or transcript timestamps. Text is split into readable captions
(~1-2 lines) without breaking words, with each caption's duration
proportional to its share of the segment.

Pure module: no I/O beyond plain strings; SRT (required) and VTT
(optional convenience) both emit UTF-8 with language-neutral escaping.
"""

from __future__ import annotations

import re
from typing import Any

from app.utils.errors import NarrationError

#: Reused caption splits are deterministic; short segments are allowed to
#: be one short cue (they are sentence groups, not flashes).


def split_caption_text(text: str, max_chars_per_caption: int) -> list[str]:
    """Split one segment's text into caption strings (word-safe)."""
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= max_chars_per_caption:
        return [text]
    chunks: list[str] = []
    for word in text.split(" "):
        if not chunks:
            chunks.append(word)
        elif len(chunks[-1]) + 1 + len(word) <= max_chars_per_caption:
            chunks[-1] = f"{chunks[-1]} {word}"
        else:
            chunks.append(word)
    return chunks


def wrap_lines(text: str, max_chars_per_line: int) -> list[str]:
    """Wrap caption text into ~1-2 display lines (word-safe)."""
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars_per_line:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _format_srt_ms(milliseconds: int) -> str:
    ms = max(0, int(milliseconds))
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _format_vtt_ms(milliseconds: int) -> str:
    ms = max(0, int(milliseconds))
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def timeline_to_cues(
    segments: list[dict[str, Any]],
    *,
    max_chars_per_caption: int,
) -> list[dict[str, Any]]:
    """Expand narration segments into subtitle cues.

    One segment usually yields one cue. Longer segments are split at word
    boundaries; the split pieces share the segment's real duration in
    proportion to their character share so subtitle timing always sums
    exactly to the measured narration.
    """
    cues: list[dict[str, Any]] = []
    for segment in segments:
        text = segment.get("text") or ""
        start_ms = int(segment["start_ms"])
        end_ms = int(segment["end_ms"])
        span = max(0, end_ms - start_ms)
        parts = split_caption_text(text, max_chars_per_caption)
        if not parts:
            continue
        total_chars = sum(len(part) for part in parts)
        cursor = start_ms
        for index, part in enumerate(parts):
            if index == len(parts) - 1:
                part_end = end_ms
            else:
                share = span * len(part) // max(1, total_chars)
                part_end = cursor + share
            if part_end <= cursor:
                part_end = cursor + 1
            cues.append({
                "start_ms": cursor,
                "end_ms": part_end,
                "text": part,
            })
            cursor = part_end
        if cues and cursor < end_ms and cues[-1]["end_ms"] < end_ms:
            cues[-1]["end_ms"] = end_ms
    return cues


def build_srt(
    segments: list[dict[str, Any]],
    *,
    max_chars_per_caption: int,
    max_chars_per_line: int,
) -> str:
    """Render the narration timeline as SRT text (UTF-8)."""
    cues = timeline_to_cues(
        segments, max_chars_per_caption=max_chars_per_caption
    )
    blocks: list[str] = []
    for number, cue in enumerate(cues, start=1):
        lines = wrap_lines(cue["text"], max_chars_per_line)
        blocks.append(
            f"{number}\n"
            f"{_format_srt_ms(cue['start_ms'])} --> {_format_srt_ms(cue['end_ms'])}\n"
            + "\n".join(lines)
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def build_vtt(
    segments: list[dict[str, Any]],
    *,
    max_chars_per_caption: int,
    max_chars_per_line: int,
) -> str:
    """Render the narration timeline as VTT text (UTF-8)."""
    cues = timeline_to_cues(
        segments, max_chars_per_caption=max_chars_per_caption
    )
    blocks: list[str] = ["WEBVTT", ""]
    for cue in cues:
        lines = wrap_lines(cue["text"], max_chars_per_line)
        blocks.append(
            f"{_format_vtt_ms(cue['start_ms'])} --> {_format_vtt_ms(cue['end_ms'])}\n"
            + "\n".join(lines)
        )
    return "\n".join(blocks) + ("\n" if len(blocks) > 2 else "")


_TIMESTAMP_RE = re.compile(
    r"(?P<sh>\d{1,2}):(?P<sm>\d{2}):(?P<ss>\d{2})[,.]"
    r"(?P<sms>\d{3})\s*-->\s*"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2}):(?P<es>\d{2})[,.]"
    r"(?P<ems>\d{3})"
)


def parse_srt(text: str) -> list[dict[str, Any]]:
    """Parse SRT text into cues; raises on malformed structure."""
    cues: list[dict[str, Any]] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if lines[0].isdigit():
            lines = lines[1:]
        if not lines:
            continue
        match = _TIMESTAMP_RE.search(lines[0])
        if not match:
            raise NarrationError(
                f"SRT contains a cue without a valid timestamp line: {block[:120]!r}"
            )
        start_ms = (
            int(match.group("sh")) * 3_600_000
            + int(match.group("sm")) * 60_000
            + int(match.group("ss")) * 1000
            + int(match.group("sms"))
        )
        end_ms = (
            int(match.group("eh")) * 3_600_000
            + int(match.group("em")) * 60_000
            + int(match.group("es")) * 1000
            + int(match.group("ems"))
        )
        body = " ".join(lines[1:])
        if not body:
            raise NarrationError(
                f"SRT cue has empty text: {block[:120]!r}"
            )
        cues.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": body,
        })
    if not cues:
        raise NarrationError("SRT contains no cues.")
    return cues


__all__ = [
    "split_caption_text",
    "wrap_lines",
    "timeline_to_cues",
    "build_srt",
    "build_vtt",
    "parse_srt",
]
