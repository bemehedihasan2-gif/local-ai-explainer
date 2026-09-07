"""Phase 6: narration segmentation unit tests (en/hi/bn)."""

from __future__ import annotations

from app.services.segmentation import segment_script, split_sentences


# ----------------------------------------------------------------------
# Sentence splitting
# ----------------------------------------------------------------------


def test_english_sentences_split_on_terminators() -> None:
    text = "First sentence here. Second one! And a third? Done."
    assert split_sentences(text) == [
        "First sentence here.",
        "Second one!",
        "And a third?",
        "Done.",
    ]


def test_abbreviations_do_not_split() -> None:
    text = "Dr. Smith lives here. He works daily from 9 a.m. to 5 p.m. sharp."
    assert split_sentences(text) == [
        "Dr. Smith lives here.",
        "He works daily from 9 a.m. to 5 p.m. sharp.",
    ]


def test_abbreviation_sentence_end_still_splits_on_next_boundary() -> None:
    # A real sentence terminator after an abbreviation/street suffix still
    # ends the sentence; the abbreviation dot itself never does.
    text = "They met on St. Mary's Ave. Then everyone left."
    sentences = split_sentences(text)
    assert sentences[0].startswith("They met on St. Mary's Ave.")
    assert "Then everyone left." in sentences[-1]


def test_initials_do_not_split() -> None:
    text = "J. R. Tolkien wrote many books. The Hobbit was first."
    assert split_sentences(text) == [
        "J. R. Tolkien wrote many books.",
        "The Hobbit was first.",
    ]


def test_numbers_and_decimals_do_not_split() -> None:
    text = "Piper runs at 22050 Hz. Version 1.2.3 works. He scored 2. Then left."
    sentences = split_sentences(text)
    assert len(sentences) == 4
    assert sentences[0] == "Piper runs at 22050 Hz."
    assert sentences[1] == "Version 1.2.3 works."


def test_hindi_danda_boundaries() -> None:
    text = "पहला वाक्य समाप्त। दूसरा वाक्य शुरू होता है! और तीसरा? बस।"
    sentences = split_sentences(text)
    assert sentences == [
        "पहला वाक्य समाप्त।",
        "दूसरा वाक्य शुरू होता है!",
        "और तीसरा?",
        "बस।",
    ]


def test_bengali_danda_boundaries() -> None:
    text = "প্রথম বাক্য শেষ। দ্বিতীয় বাক্য শুরু হলো। শেষ বাক্য!"
    sentences = split_sentences(text)
    assert sentences == ["প্রথম বাক্য শেষ।", "দ্বিতীয় বাক্য শুরু হলো।", "শেষ বাক্য!"]


def test_empty_and_whitespace_text() -> None:
    assert split_sentences("") == []
    assert split_sentences("   ") == []


# ----------------------------------------------------------------------
# Segment packing
# ----------------------------------------------------------------------

_SCRIPT = {
    "schema_version": 1,
    "language": "en",
    "sections": [
        {
            "scene_ids": [1, 2],
            "purpose": "hook",
            "word_budget": 40,
            "text": (
                "This tutorial shows the whole recipe from start to finish. "
                "The presenter introduces the dish and lays out every "
                "ingredient on the counter before cooking begins."
            ),
        },
        {
            "scene_ids": [3, 4, 5],
            "purpose": "main_steps",
            "word_budget": 60,
            "text": (
                "A key technique appears mid-way when the mixture must be "
                "folded carefully. The oven is preheated and the cake bakes "
                "until it turns golden brown on top."
            ),
        },
        {
            "scene_ids": [6],
            "purpose": "ending",
            "word_budget": 20,
            "text": "The finished dish is plated and presented to the camera.",
        },
    ],
}


def test_segments_preserve_sections_and_scene_ids() -> None:
    doc = segment_script(_SCRIPT, max_segment_chars=140, max_segment_words=18)
    assert doc["count"] == len(doc["segments"])
    assert doc["segments"][0]["scene_ids"] == [1, 2]
    assert doc["segments"][0]["section"] == "hook"
    # Scene ids of the middle section survive on its segments.
    middle_ids = {
        seg["scene_ids"][0]
        for seg in doc["segments"]
        if seg["section"] == "main_steps"
    }
    assert middle_ids <= {3, 4, 5}
    # Segment ids are contiguous from 1.
    assert [seg["segment_id"] for seg in doc["segments"]] == list(
        range(1, doc["count"] + 1)
    )


def test_packing_respects_bounds() -> None:
    doc = segment_script(_SCRIPT, max_segment_chars=60, max_segment_words=12)
    for segment in doc["segments"]:
        assert len(segment["text"]) <= 60 + 2  # join-space tolerance
        assert segment["words"] <= 12 + 2


def test_no_word_is_split_by_packing() -> None:
    doc = segment_script(_SCRIPT, max_segment_chars=50, max_segment_words=8)
    words = " ".join(seg["text"] for seg in doc["segments"]).split()
    source_words = " ".join(
        section["text"] for section in _SCRIPT["sections"]
    ).split()
    assert words == source_words


def test_empty_section_is_flagged_not_fatal() -> None:
    script = dict(_SCRIPT)
    script["sections"] = [
        {"scene_ids": [1], "purpose": "empty", "word_budget": 5, "text": "   "},
        {"scene_ids": [2], "purpose": "ok", "word_budget": 5, "text": "A real sentence here."},
    ]
    doc = segment_script(script)
    assert doc["count"] >= 1
    assert any("no text" in warning for warning in doc["warnings"])


def test_fully_empty_script_warns() -> None:
    doc = segment_script({"language": "en", "sections": []})
    assert doc["count"] == 0
    assert doc["warnings"]
