"""Phase 6: SRT/VTT generation + parsing tests (English/Hindi/Bengali)."""

from __future__ import annotations

import pytest

from app.services.subtitles import (
    build_srt,
    build_vtt,
    parse_srt,
    split_caption_text,
    timeline_to_cues,
    wrap_lines,
)
from app.utils.errors import NarrationError

SEGMENTS_EN = [
    {
        "segment_id": 1,
        "start_ms": 0,
        "end_ms": 4210,
        "text": "This video explains the whole process from start to finish.",
    },
    {
        "segment_id": 2,
        "start_ms": 4410,
        "end_ms": 9020,
        "text": "A key detail appears halfway through the demonstration.",
    },
]

SEGMENTS_BN = [
    {
        "segment_id": 1,
        "start_ms": 0,
        "end_ms": 3500,
        "text": "এই ভিডিওটি প্রথম থেকে শেষ পর্যন্ত পুরো প্রক্রিয়াটি ব্যাখ্যা করে।",
    },
    {
        "segment_id": 2,
        "start_ms": 3800,
        "end_ms": 7100,
        "text": "মাঝপথে একটি গুরুত্বপূর্ণ বিবরণ দেখা যায়।",
    },
]

SEGMENTS_HI = [
    {
        "segment_id": 1,
        "start_ms": 0,
        "end_ms": 3100,
        "text": "यह वीडियो शुरू से अंत तक पूरी प्रक्रिया समझाता है।",
    },
]


def test_srt_uses_actual_measured_timing() -> None:
    srt = build_srt(
        SEGMENTS_EN,
        max_chars_per_caption=84,
        max_chars_per_line=42,
    )
    cues = parse_srt(srt)
    # Segment timestamps come from the measured narration timeline as-is
    # (segment 2 starts 200 ms after segment 1 ended - the inter-segment
    # gap was already baked into the timeline).
    assert [cue["start_ms"] for cue in cues] == [0, 4410]
    assert [cue["end_ms"] for cue in cues] == [4210, 9020]
    assert "This video explains" in cues[0]["text"]


def test_srt_bengali_utf8_roundtrip() -> None:
    srt = build_srt(
        SEGMENTS_BN,
        max_chars_per_caption=84,
        max_chars_per_line=42,
    )
    # Raw bytes are valid UTF-8 with the Bengali text intact.
    assert "ব্যাখ্যা করে" in srt
    cues = parse_srt(srt)
    assert "ব্যাখ্যা করে" in cues[0]["text"]


def test_srt_hindi_utf8_roundtrip() -> None:
    srt = build_srt(
        SEGMENTS_HI,
        max_chars_per_caption=84,
        max_chars_per_line=42,
    )
    assert "समझाता है" in srt
    cues = parse_srt(srt)
    assert cues[0]["text"] == SEGMENTS_HI[0]["text"]


def test_vtt_has_webvtt_header_and_dot_millis() -> None:
    vtt = build_vtt(
        SEGMENTS_EN,
        max_chars_per_caption=84,
        max_chars_per_line=42,
    )
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:04.210" in vtt


def test_timeline_to_cues_splits_long_text_proportionally() -> None:
    long_text = "word " * 60  # 240 chars, far above the caption limit
    segments = [{
        "segment_id": 1, "start_ms": 0, "end_ms": 12000, "text": long_text.strip(),
    }]
    cues = timeline_to_cues(segments, max_chars_per_caption=84)
    assert len(cues) > 1
    assert all(len(cue["text"]) <= 84 for cue in cues)
    assert cues[0]["start_ms"] == 0
    assert cues[-1]["end_ms"] == 12000
    # Cues are contiguous and ordered.
    for previous, current in zip(cues, cues[1:]):
        assert previous["end_ms"] <= current["start_ms"]


def test_word_wrap_respects_line_limit() -> None:
    lines = wrap_lines("This is a fairly long caption line of text.", 20)
    assert all(len(line) <= 20 for line in lines)
    assert " ".join(lines) == "This is a fairly long caption line of text."


def test_split_caption_text_never_splits_words() -> None:
    chunks = split_caption_text(
        "one two three four five six seven eight nine ten", 18,
    )
    assert all(len(chunk) <= 18 for chunk in chunks)
    assert " ".join(chunks) == "one two three four five six seven eight nine ten"


def test_parse_srt_rejects_broken_syntax() -> None:
    with pytest.raises(NarrationError):
        parse_srt("1\nthis has no timestamp line at all")
    with pytest.raises(NarrationError):
        parse_srt(
            "1\n00:00:00,000 --> 00:00:01,000\n\n"  # empty text cue
            "2\n00:00:01,000 --> 00:00:02,000\nhello"
        )


def test_timestamps_are_monotonic_and_valid() -> None:
    srt = build_srt(
        SEGMENTS_EN,
        max_chars_per_caption=84,
        max_chars_per_line=42,
    )
    for cue in parse_srt(srt):
        assert cue["start_ms"] >= 0
        assert cue["end_ms"] > cue["start_ms"]
