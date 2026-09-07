"""Phase 5: story pipeline service tests (deterministic, no real LLM).

A fake ``LocalLLMProvider`` (scripted JSON responses) stands in for
llama.cpp, so the whole planning pipeline is exercised without a model:
evidence preparation, batched story understanding, importance scoring +
selection, duration/script planning, script generation and the
deterministic quality checks.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from app.ai.story import StoryUnderstandingService
from app.config import Settings
from app.services.duration_planning import DurationPlanningService
from app.services.evidence import EvidencePreparationService
from app.services.scene_importance import SceneImportanceService
from app.services.script_quality import ScriptQualityService
from app.services.storage import StorageService
from app.utils.errors import (
    EvidenceError,
    ScriptQualityError,
)

# ----------------------------------------------------------------------
# Fake LLM provider
# ----------------------------------------------------------------------


class FakeProvider:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = deque(responses or [])
        self.calls: list[str] = []
        self.available_flag = True

    def available(self) -> bool:
        return self.available_flag

    def generate(self, prompt: str, *, max_tokens: int, temperature: float = 0.2) -> str:
        self.calls.append(prompt)
        if not self.responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        return self.responses.popleft()

    def describe(self) -> dict:
        return {
            "provider": "llama_cpp", "available": self.available_flag,
            "executable_available": True, "model_available": True,
            "model_name": "fake.gguf", "threads": 4, "context_size": 2048,
            "max_tokens": 1024, "temperature": 0.2, "setup_hint": None,
        }


BATCH_SUMMARY_JSON = json.dumps({
    "events": [
        {"text": "A tutorial introduces a recipe", "scene_ids": [1]},
        {"text": "Ingredients are prepared on screen", "scene_ids": [2, 3]},
    ],
    "facts": [
        {"text": "Each step is demonstrated with on-screen text", "scene_ids": [2]},
    ],
    "entities": ["The presenter"],
    "uncertain": [],
})

STORY_JSON = json.dumps({
    "content_type": "tutorial",
    "content_type_confidence": 0.85,
    "premise": "A cooking tutorial demonstrates a recipe step by step.",
    "main_entities": ["The presenter"],
    "locations": [],
    "chronological_events": [
        {"text": "The presenter introduces the recipe", "scene_ids": [1]},
        {"text": "Ingredients are measured", "scene_ids": [2, 3]},
        {"text": "The dish is finished", "scene_ids": [6]},
    ],
    "key_turning_points": [
        {"text": "A key mixing technique is shown", "scene_ids": [4]},
    ],
    "beginning": [{"text": "The video opens with an introduction", "scene_ids": [1]}],
    "middle": [{"text": "The main steps are shown", "scene_ids": [3, 4]}],
    "ending": [{"text": "The finished dish is presented", "scene_ids": [6]}],
    "cause_effect": [
        {"cause": "the oven is preheated", "effect": "the cake bakes evenly",
         "scene_ids": [5]},
    ],
    "important_facts": [
        {"text": "The recipe serves four people", "scene_ids": [2]},
    ],
    "uncertain_points": [],
    "evidence_scene_ids": [1, 2, 3, 4, 5, 6],
})

SCRIPT_PARAGRAPHS = (
    "This tutorial shows how to make the dish from start to finish. "
    "The presenter first introduces the recipe and gathers the ingredients "
    "shown on screen.\n\n"
    "A key technique appears mid-way, when the mixture must be folded "
    "carefully. The video then finishes with the dish presented and ready "
    "to serve."
)

_SUBJECTS = ["the presenter", "the video", "the tutorial", "the on-screen text",
             "the narrator", "the viewer", "the recipe", "the finished dish"]
_VERBS = ["explains", "demonstrates", "shows", "describes", "highlights",
          "covers", "moves through", "finishes with"]
_OBJECTS = ["the ingredients", "each cooking step", "the key technique",
            "the timing", "the temperature", "the final result",
            "the measurements", "the serving suggestion"]


def _distinct_sentences() -> list[str]:
    """~64 deterministic, distinct, evidence-plausible sentences."""
    return [
        f"{_SUBJECTS[i % 8].capitalize()} {_VERBS[(i // 8) % 8]} "
        f"{_OBJECTS[(i // 64) % 8]} in the cooking demonstration."
        for i in range(64)
    ]


def _script_with_words(target: int) -> str:
    """Build a full_text with ~``target`` words from distinct sentences."""
    sentences = _distinct_sentences()
    chosen: list[str] = []
    total = 0
    while total < target:
        for sentence in sentences:
            chosen.append(sentence)
            total += len(sentence.split())
            if total >= target:
                break
    return " ".join(chosen)


# ----------------------------------------------------------------------
# Synthetic analyzed project (Phase 4 artifacts on disk)
# ----------------------------------------------------------------------


def _scene(index: int, start: float, end: float) -> dict:
    return {
        "scene_id": index,
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "representative_timestamp": round((start + end) / 2, 3),
        "representative_frame": f"frames/scene_{index - 1:03d}.jpg",
    }


def _write_phase4_artifacts(settings: Settings, project_id: str) -> None:
    storage = StorageService(settings)
    storage.ensure_project_dirs(project_id)
    metadata = storage.project_path(project_id, "analysis", "metadata")
    metadata.mkdir(parents=True, exist_ok=True)

    scenes = [
        _scene(1, 0.0, 6.0), _scene(2, 6.0, 12.0), _scene(3, 12.0, 18.0),
        _scene(4, 18.0, 24.0), _scene(5, 24.0, 30.0), _scene(6, 30.0, 36.0),
    ]
    segments = [
        {"id": 0, "start": 1.0, "end": 4.0, "text": "welcome to the tutorial",
         "confidence": 0.9},
        {"id": 1, "start": 8.0, "end": 11.0, "text": "first measure the flour",
         "confidence": 0.88},
        {"id": 2, "start": 14.0, "end": 17.0, "text": "add the eggs and sugar",
         "confidence": 0.91},
        {"id": 3, "start": 20.0, "end": 23.0, "text": "fold the mixture gently",
         "confidence": 0.85},
        {"id": 4, "start": 26.0, "end": 29.0, "text": "preheat the oven now",
         "confidence": 0.87},
        {"id": 5, "start": 32.0, "end": 35.0, "text": "serve and enjoy the dish",
         "confidence": 0.9},
    ]
    ocr = [
        {"timestamp": 2.0, "text": "RECIPE TITLE", "confidence": 0.7},
        {"timestamp": 9.0, "text": "STEP 1: MEASURE", "confidence": 0.8},
        {"timestamp": 21.0, "text": "FOLD GENTLY", "confidence": 0.75},
    ]
    visual_frames = [
        {"timestamp": 3.0, "frame_path": "frames/scene_000.jpg",
         "brightness": 120.0, "blur_estimate": 0.2, "complexity": 0.6,
         "width": 64, "height": 36},
        {"timestamp": 9.0, "frame_path": "frames/scene_001.jpg",
         "brightness": 140.0, "blur_estimate": 0.15, "complexity": 0.7,
         "width": 64, "height": 36},
        {"timestamp": 15.0, "frame_path": "frames/scene_002.jpg",
         "brightness": 130.0, "blur_estimate": 0.25, "complexity": 0.65,
         "width": 64, "height": 36},
        {"timestamp": 21.0, "frame_path": "frames/scene_003.jpg",
         "brightness": 110.0, "blur_estimate": 0.3, "complexity": 0.6,
         "width": 64, "height": 36},
        {"timestamp": 27.0, "frame_path": "frames/scene_004.jpg",
         "brightness": 150.0, "blur_estimate": 0.2, "complexity": 0.55,
         "width": 64, "height": 36},
        {"timestamp": 33.0, "frame_path": "frames/scene_005.jpg",
         "brightness": 160.0, "blur_estimate": 0.18, "complexity": 0.5,
         "width": 64, "height": 36},
    ]

    def put(name: str, document: dict) -> None:
        (metadata / name).write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )

    put("scenes.json", {
        "schema_version": 1, "duration_seconds": 36.0, "scenes": scenes,
    })
    put("transcript.json", {
        "schema_version": 1, "language": "en", "language_probability": 0.95,
        "duration_seconds": 36.0, "segments": segments, "warnings": [],
    })
    put("ocr.json", {"schema_version": 1, "frames": ocr})
    put("visual.json", {
        "provider": "deterministic", "frames": visual_frames,
    })
    put("timeline.json", {
        "schema_version": 1, "duration_seconds": 36.0,
        "summary": {"scene_count": 6, "speech_scenes": 6, "ocr_scenes": 3,
                    "total_words": 30, "detected_language": "en"},
        "scenes": [
            {"scene_id": i, "start": s["start"], "end": s["end"],
             "duration": s["duration"],
             "representative_timestamp": s["representative_timestamp"],
             "representative_frame": s["representative_frame"],
             "speech_present": True, "speech": [], "ocr_present": True,
             "ocr": [], "visual": visual_frames[i - 1],
             "information_density": 60 + (i % 3) * 10}
            for i, s in enumerate(scenes, start=1)
        ],
    })
    put("analysis_manifest.json", {
        "schema_version": 1,
        "results": {"transcript_available": True, "ocr_available": True,
                    "visual_provider": "deterministic",
                    "detected_language": "en"},
        "warnings": [],
    })


@pytest.fixture
def analyzed_project(settings, tmp_path):
    project_id = "p5testproject"
    _write_phase4_artifacts(settings, project_id)
    row = {
        "id": project_id, "sha256": "abc123", "duration": 36.0,
        "original_filename": "cooking.mp4", "language": "en",
        "target_duration_seconds": 180,
        "analysis_width": 64, "analysis_height": 36, "analysis_fps": 5.0,
    }
    return row, settings, tmp_path


def _run_story_pipeline(settings, project_row, provider, *, language="en", duration=180):
    """Drive the full Phase 5 pipeline with a fake provider; returns all docs."""
    from app.ai.script import ScriptGenerationService
    from app.ai.story import StoryUnderstandingService

    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    story, batches = StoryUnderstandingService(settings, provider).understand(evidence)
    importance = SceneImportanceService(settings)
    importance_doc = importance.score(evidence, story)
    selection_doc = importance.select(evidence, story, importance_doc)
    duration_plan, script_plan = DurationPlanningService(settings).plan(
        evidence, story, selection_doc, duration,
    )
    script = ScriptGenerationService(settings, provider).generate_script(
        script_plan, story, evidence, language,
    )
    quality = ScriptQualityService(settings).check(script, duration_plan, story, evidence)
    return {
        "evidence": evidence, "story": story, "batches": batches,
        "importance": importance_doc, "selection": selection_doc,
        "duration_plan": duration_plan, "script_plan": script_plan,
        "script": script, "quality": quality,
    }


# ----------------------------------------------------------------------
# Evidence preparation
# ----------------------------------------------------------------------


def test_evidence_manifest_is_bounded_and_relative(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    assert evidence["schema_version"] == 1
    assert evidence["summary"]["scene_count"] == 6
    assert evidence["summary"]["has_speech"] is True
    assert evidence["summary"]["has_ocr"] is True
    assert evidence["summary"]["total_transcript_words"] == 26

    limit = settings.story_max_excerpt_chars
    for scene in evidence["scenes"]:
        assert len(scene["transcript_excerpt"]) <= limit + 1  # + ellipsis char
        assert len(scene["ocr_text"]) <= limit + 1
        assert 0.0 <= scene["speech_density"] <= 1.0
        assert scene["previous_scene"] is None or scene["previous_scene"] < scene["scene_id"]
        assert scene["next_scene"] is None or scene["next_scene"] > scene["scene_id"]
        assert scene["representative_frame"].startswith("frames/")
    assert evidence["scenes"][0]["previous_scene"] is None
    assert evidence["scenes"][-1]["next_scene"] is None
    # No absolute filesystem paths in the manifest.
    assert str(settings.base_dir) not in json.dumps(evidence)


def test_evidence_missing_scenes_raises(analyzed_project, tmp_path) -> None:
    project_row, settings, _ = analyzed_project
    metadata = settings.projects_dir / project_row["id"] / "analysis" / "metadata"
    (metadata / "scenes.json").unlink()
    with pytest.raises(EvidenceError):
        EvidencePreparationService(settings, StorageService(settings)).prepare(
            project_row["id"], project_row
        )


# ----------------------------------------------------------------------
# Story understanding
# ----------------------------------------------------------------------


def test_story_understanding_structured_and_grounded(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON])
    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    story, batches = StoryUnderstandingService(settings, provider).understand(evidence)

    assert story["content_type"] == "tutorial"
    assert 0.0 <= story["content_type_confidence"] <= 1.0
    assert story["premise"]
    assert story["chronological_events"][0]["scene_ids"] == [1]
    assert story["key_turning_points"][0]["scene_ids"] == [4]
    assert set(story["evidence_scene_ids"]) <= {1, 2, 3, 4, 5, 6}
    assert len(batches) == 1
    assert batches[0]["events"][0]["scene_ids"] == [1]
    # Deterministic semantic scores: 0..1 and turning-point scene boosted.
    scores = story["scene_scores"]
    assert all(0.0 <= value <= 1.0 for value in scores.values())
    assert scores["4"] > scores["1"]


def test_story_batches_long_videos(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    settings.story_batch_scenes = 15
    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    # Inflate to 20 scenes: 15 + 5 -> two batches + one global call.
    extra = []
    for index in range(7, 21):
        extra.append({
            "scene_id": index,
            "start": 36.0 + index, "end": 37.0 + index,
            "duration": 1.0, "previous_scene": index - 1,
            "next_scene": index + 1 if index < 20 else None,
            "transcript_excerpt": f"scene {index} says something",
            "transcript_words": 4, "ocr_text": "",
            "visual": None, "information_density": 40,
            "speech_density": 0.5, "representative_frame": None,
        })
    evidence["scenes"].extend(extra)
    evidence["summary"]["scene_count"] = 20

    provider = FakeProvider([
        BATCH_SUMMARY_JSON, BATCH_SUMMARY_JSON, STORY_JSON,
    ])
    story, batches = StoryUnderstandingService(settings, provider).understand(evidence)
    assert len(batches) == 2
    assert len(provider.calls) == 3  # 2 batches + 1 global story
    assert story["content_type"] == "tutorial"


def test_story_invalid_ids_and_content_type_are_filtered(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    weird = json.loads(STORY_JSON)
    weird["content_type"] = "alien-romance"
    weird["chronological_events"][0]["scene_ids"] = [1, 99, "2"]
    weird["evidence_scene_ids"] = [99, 1]
    provider = FakeProvider([BATCH_SUMMARY_JSON, json.dumps(weird)])
    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    story, _ = StoryUnderstandingService(settings, provider).understand(evidence)
    assert story["content_type"] == "general"
    assert story["content_type_confidence"] <= 0.5
    assert story["chronological_events"][0]["scene_ids"] == [1, 2]
    assert story["evidence_scene_ids"] == [1]


def test_story_unavailable_provider_fails_honestly(analyzed_project) -> None:
    from app.utils.errors import LLMUnavailableError

    project_row, settings, _ = analyzed_project
    provider = FakeProvider([])
    provider.available_flag = False
    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    with pytest.raises(LLMUnavailableError):
        StoryUnderstandingService(settings, provider).understand(evidence)


# ----------------------------------------------------------------------
# Scene importance & selection
# ----------------------------------------------------------------------


def _importance_docs(analyzed_project):
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON])
    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    story, _ = StoryUnderstandingService(settings, provider).understand(evidence)
    service = SceneImportanceService(settings)
    return evidence, story, service.score(evidence, story), service.select(
        evidence, story, service.score(evidence, story)
    )


def test_importance_weights_are_documented_and_normalized(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    service = SceneImportanceService(settings)
    weights = service.weights()
    additive = (
        weights["information_density"] + weights["speech_density"]
        + weights["semantic"] + weights["turning_point"]
        + weights["continuity"] + weights["ocr"]
    )
    assert abs(additive - 0.95) < 1e-9
    assert weights["redundancy_adjustment"] == 0.05


def test_importance_scores_bounded_with_reasons(analyzed_project) -> None:
    evidence, story, importance_doc, _ = _importance_docs(analyzed_project)
    scored = importance_doc["scenes"]
    assert len(scored) == 6
    for item in scored:
        assert 0.0 <= item["importance_score"] <= 1.0
        assert item["reasons"], "every scene needs a reason"
    # Turning-point scene 4 must outscore a plain mid scene.
    by_id = {item["scene_id"]: item for item in scored}
    assert by_id[4]["importance_score"] > by_id[5]["importance_score"]


def test_selection_keeps_anchors_and_covers_timeline(analyzed_project) -> None:
    evidence, story, importance_doc, selection = _importance_docs(analyzed_project)
    ids = [row["scene_id"] for row in selection["selected"]]
    assert ids[0] == 1 and ids[-1] == 6  # opening + closing always kept
    assert len(ids) <= settings_max_scenes(evidence)
    assert selection["summary"]["selected_count"] == len(ids)
    ranks = [row["selected_rank"] for row in selection["scenes"] if row["selected"]]
    assert ranks == sorted(ranks)


def settings_max_scenes(evidence) -> int:
    return 24


def test_redundant_consecutive_scenes_are_removed(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON])
    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    # Make scenes 2 and 3 textually identical (near-duplicate evidence).
    evidence["scenes"][1]["transcript_excerpt"] = "repeat the same step twice"
    evidence["scenes"][1]["ocr_text"] = "STEP AGAIN"
    evidence["scenes"][2]["transcript_excerpt"] = "repeat the same step twice"
    evidence["scenes"][2]["ocr_text"] = "STEP AGAIN"
    story, _ = StoryUnderstandingService(settings, provider).understand(evidence)
    service = SceneImportanceService(settings)
    importance_doc = service.score(evidence, story)
    selection = service.select(evidence, story, importance_doc)
    ids = [row["scene_id"] for row in selection["selected"]]
    # 1 and 6 are anchors; at most one of the duplicate pair survives.
    kept_duplicates = [sid for sid in (2, 3) if sid in ids]
    assert len(kept_duplicates) <= 1


# ----------------------------------------------------------------------
# Duration planning
# ----------------------------------------------------------------------


def test_duration_plan_budgets_within_target_range(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON])
    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    story, _ = StoryUnderstandingService(settings, provider).understand(evidence)
    service = SceneImportanceService(settings)
    selection = service.select(evidence, story, service.score(evidence, story))

    for duration, (low, high) in settings.script_word_targets.items():
        duration_plan, script_plan = DurationPlanningService(settings).plan(
            evidence, story, selection, duration,
        )
        total = duration_plan["total_word_budget"]
        assert low <= total <= high, (duration, total)
        assert duration_plan["target_duration_seconds"] == duration
        assert duration_plan["estimated_duration_seconds"] == round(total / settings.narration_wpm * 60)
        for row in duration_plan["scenes"]:
            assert row["word_budget"] >= 0
            assert row["word_budget"] <= settings.max_words_per_scene
        assert script_plan["total_word_budget"] == total
        assert script_plan["target_duration_seconds"] == duration
        # Sections move forward in time.
        flat = [sid for section in script_plan["sections"] for sid in section["scene_ids"]]
        assert flat == sorted(flat)
        assert flat[0] == 1 and flat[-1] == 6


def test_duration_plan_skips_evidence_less_scenes(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON])
    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    # Strip all evidence from scene 3: continuity-only, no narration budget.
    evidence["scenes"][2]["transcript_excerpt"] = ""
    evidence["scenes"][2]["ocr_text"] = ""
    evidence["scenes"][2]["visual"] = None
    story, _ = StoryUnderstandingService(settings, provider).understand(evidence)
    service = SceneImportanceService(settings)
    selection = service.select(evidence, story, service.score(evidence, story))
    duration_plan, _ = DurationPlanningService(settings).plan(
        evidence, story, selection, 180,
    )
    budget_by_id = {row["scene_id"]: row["word_budget"] for row in duration_plan["scenes"]}
    assert budget_by_id[3] == 0
    assert sum(budget_by_id.values()) >= 375


def test_duration_plan_falls_back_for_silent_video(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON])
    evidence = EvidencePreparationService(settings, StorageService(settings)).prepare(
        project_row["id"], project_row
    )
    for scene in evidence["scenes"]:
        scene["transcript_excerpt"] = ""
        scene["ocr_text"] = ""
        scene["visual"] = None
    story, _ = StoryUnderstandingService(settings, provider).understand(evidence)
    service = SceneImportanceService(settings)
    selection = service.select(evidence, story, service.score(evidence, story))
    duration_plan, _ = DurationPlanningService(settings).plan(
        evidence, story, selection, 120,
    )
    assert all(row["word_budget"] > 0 for row in duration_plan["scenes"])
    assert 250 <= duration_plan["total_word_budget"] <= 300


# ----------------------------------------------------------------------
# Script generation
# ----------------------------------------------------------------------


def test_script_generation_structure_and_language(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON, SCRIPT_PARAGRAPHS])
    docs = _run_story_pipeline(settings, project_row, provider, language="en", duration=180)
    script = docs["script"]
    assert script["language"] == "en"
    assert script["language_label"] == "English"
    assert script["content_type"] == "tutorial"
    assert script["full_text"]
    assert script["word_count"] > 20
    assert script["estimated_duration_seconds"] == round(
        script["word_count"] / settings.narration_wpm * 60
    )
    assert len(script["sections"]) >= 2
    for section in script["sections"]:
        assert section["scene_ids"]
    # Sections cover the anchors: first section begins with scene 1.
    assert script["sections"][0]["scene_ids"][0] == 1


def test_script_paragraph_mismatch_is_flagged(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON, "Only one paragraph here."])
    docs = _run_story_pipeline(settings, project_row, provider, language="en", duration=180)
    assert any("paragraph" in warning for warning in docs["script"]["warnings"])
    assert any(not section["text"] for section in docs["script"]["sections"])


# ----------------------------------------------------------------------
# Quality control
# ----------------------------------------------------------------------


def test_quality_rejects_empty_script(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON, SCRIPT_PARAGRAPHS])
    docs = _run_story_pipeline(settings, project_row, provider)
    docs["script"]["full_text"] = ""
    docs["script"]["word_count"] = 0
    with pytest.raises(ScriptQualityError):
        ScriptQualityService(settings).check(
            docs["script"], docs["duration_plan"], docs["story"], docs["evidence"],
        )


def _fill_sections(docs: dict, target_words: int) -> None:
    """Replace the short fake draft with a well-sized narration where every
    section carries text (so QC only judges the narration itself)."""
    full = _script_with_words(target_words)
    words = full.split()
    sections = docs["script"]["sections"]
    chunk = max(1, len(words) // len(sections))
    docs["script"]["sections"] = [
        {
            "scene_ids": section["scene_ids"],
            "purpose": section["purpose"],
            "word_budget": section["word_budget"],
            "text": " ".join(
                words[index * chunk : (index + 1) * chunk if index < len(sections) - 1 else len(words)]
            ),
        }
        for index, section in enumerate(sections)
    ]
    docs["script"]["full_text"] = full
    docs["script"]["word_count"] = len(words)


def test_quality_good_script_scores_high(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON, SCRIPT_PARAGRAPHS])
    docs = _run_story_pipeline(settings, project_row, provider, duration=180)
    # Replace the draft with a well-sized original narration (~target mid).
    _fill_sections(docs, 412)
    quality = ScriptQualityService(settings).check(
        docs["script"], docs["duration_plan"], docs["story"], docs["evidence"],
    )
    assert 0 <= quality["quality_score"] <= 100
    assert quality["quality_score"] >= 85
    assert quality["scores"]["language_score"] == 100
    assert quality["scores"]["chronology_score"] == 100
    assert quality["scores"]["duration_fit_score"] >= 90
    assert quality["scores"]["grounding_score"] >= 80
    assert quality["checks"][0]["check"] == "language"
    assert quality["estimated_duration_seconds"] > 0
    # Formula documented in the output.
    assert abs(sum(quality["formula"].values()) - 1.0) < 1e-9


def test_quality_language_mismatch_penalizes(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON, SCRIPT_PARAGRAPHS])
    docs = _run_story_pipeline(settings, project_row, provider, language="hi", duration=180)
    _fill_sections(docs, 412)
    quality = ScriptQualityService(settings).check(
        docs["script"], docs["duration_plan"], docs["story"], docs["evidence"],
    )
    assert quality["scores"]["language_score"] == 40
    assert quality["quality_score"] < 100
    assert any("Hindi" in warning for warning in quality["warnings"])


def test_quality_source_copying_is_detected(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON, SCRIPT_PARAGRAPHS])
    docs = _run_story_pipeline(settings, project_row, provider)
    # Copy a long transcript phrase verbatim.
    docs["script"]["full_text"] += (
        "\n\nwelcome to the tutorial first measure the flour add the eggs "
        "and sugar fold the mixture gently"
    )
    docs["script"]["word_count"] = len(docs["script"]["full_text"].split())
    quality = ScriptQualityService(settings).check(
        docs["script"], docs["duration_plan"], docs["story"], docs["evidence"],
    )
    assert quality["scores"]["originality_score"] < 100
    assert any("verbatim" in warning for warning in quality["warnings"])


def test_quality_repetition_is_detected(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON, SCRIPT_PARAGRAPHS])
    docs = _run_story_pipeline(settings, project_row, provider)
    docs["script"]["full_text"] += (
        "\n\nThe mixture is folded gently into the batter. The mixture is "
        "folded gently into the batter again."
    )
    docs["script"]["word_count"] = len(docs["script"]["full_text"].split())
    quality = ScriptQualityService(settings).check(
        docs["script"], docs["duration_plan"], docs["story"], docs["evidence"],
    )
    assert quality["scores"]["coherence_score"] < 100
    assert any("duplicate" in warning for warning in quality["warnings"])


def test_quality_chronology_violation_is_detected(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON, SCRIPT_PARAGRAPHS])
    docs = _run_story_pipeline(settings, project_row, provider)
    docs["script"]["sections"] = list(reversed(docs["script"]["sections"]))
    quality = ScriptQualityService(settings).check(
        docs["script"], docs["duration_plan"], docs["story"], docs["evidence"],
    )
    assert quality["scores"]["chronology_score"] == 50
    assert any("chronological" in warning for warning in quality["warnings"])


def test_quality_flags_unsupported_numbers(analyzed_project) -> None:
    project_row, settings, _ = analyzed_project
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON, SCRIPT_PARAGRAPHS])
    docs = _run_story_pipeline(settings, project_row, provider)
    docs["script"]["full_text"] += "\n\nThis recipe won 15 awards in 2019."
    docs["script"]["word_count"] = len(docs["script"]["full_text"].split())
    quality = ScriptQualityService(settings).check(
        docs["script"], docs["duration_plan"], docs["story"], docs["evidence"],
    )
    assert quality["scores"]["grounding_score"] < 100
    assert any("2019" in warning for warning in quality["warnings"])


# ----------------------------------------------------------------------
# Generation fingerprint
# ----------------------------------------------------------------------


def test_generation_fingerprint_tracks_options(settings) -> None:
    from app.utils.fingerprints import story_generation_fingerprint

    row = {"sha256": "abc", "duration": 36.0, "analysis_width": 64,
           "analysis_height": 36, "analysis_fps": 5.0}
    base = story_generation_fingerprint(settings, row, language="en", target_duration_seconds=180)
    assert base == story_generation_fingerprint(settings, row, language="en", target_duration_seconds=180)
    assert base != story_generation_fingerprint(settings, row, language="hi", target_duration_seconds=180)
    assert base != story_generation_fingerprint(settings, row, language="en", target_duration_seconds=240)
    settings.narration_wpm = 160
    assert base != story_generation_fingerprint(settings, row, language="en", target_duration_seconds=180)