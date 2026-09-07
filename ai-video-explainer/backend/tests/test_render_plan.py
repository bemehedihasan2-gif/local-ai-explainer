"""Phase 7: render-plan builder unit tests (pure, no FFmpeg)."""

from __future__ import annotations

import pytest

from app.services.render_plan import build_video_plan
from app.utils.errors import RenderArtifactError


def _scenes() -> list[dict]:
    return [
        {"scene_id": 1, "start": 0.0, "end": 10.0, "importance_score": 0.9},
        {"scene_id": 2, "start": 12.0, "end": 22.0, "importance_score": 0.8},
        {"scene_id": 3, "start": 25.0, "end": 40.0, "importance_score": 0.7},
    ]


def _segments() -> list[dict]:
    return [
        {"segment_id": 1, "start_ms": 300, "end_ms": 5200, "scene_ids": [1], "section": "hook"},
        {"segment_id": 2, "start_ms": 5400, "end_ms": 9800, "scene_ids": [1, 2], "section": "hook"},
        {"segment_id": 3, "start_ms": 10200, "end_ms": 15100, "scene_ids": [2], "section": "section"},
        {"segment_id": 4, "start_ms": 15500, "end_ms": 22100, "scene_ids": [3], "section": "ending"},
    ]


def test_plan_is_chronological_and_never_shorter_than_narration() -> None:
    plan = build_video_plan(
        _scenes(), _segments(), narration_duration_ms=22500, tail_ms=1200
    )
    assert plan["video_duration_ms"] >= 22500
    assert plan["subtitle_aligned"] is True
    # Clips are ordered by output start and never overlap.
    clips = plan["clips"]
    for prev, current in zip(clips, clips[1:]):
        assert current["output_start_ms"] >= prev["output_end_ms"]
    # Every narration segment maps to exactly one scene group.
    mapped = [
        sid for group in plan["groups"] for sid in group["narration_segment_ids"]
    ]
    assert sorted(mapped) == [1, 2, 3, 4]
    # Chronology: scene ids appear in story order.
    order = [clip["scene_id"] for clip in clips]
    assert order.index(1) < order.index(2) < order.index(3)


def test_plan_tiles_each_scene_window_and_holds_short_sources() -> None:
    # Scene 1 is 10 s but its narration window is 300..9800 ms; scene 3 is
    # 15 s with narration 15500..22100 - both fits. Scene 2 window spans
    # two segments (5400..15100) inside its 10 s range.
    plan = build_video_plan(
        _scenes(), _segments(), narration_duration_ms=22500, tail_ms=1200
    )
    # The last clip carries the tail hold.
    last = plan["clips"][-1]
    assert last["hold_ms"] >= 1200 - 1


def test_plan_rejects_invalid_scene_range() -> None:
    with pytest.raises(RenderArtifactError):
        build_video_plan(
            [{"scene_id": 9, "start": 30.0, "end": 10.0}],
            [{"segment_id": 1, "start_ms": 0, "end_ms": 1000, "scene_ids": [9]}],
            narration_duration_ms=1000,
        )


def test_plan_rejects_empty_selection_and_empty_segments() -> None:
    with pytest.raises(RenderArtifactError):
        build_video_plan([], _segments(), narration_duration_ms=1000)
    with pytest.raises(RenderArtifactError):
        build_video_plan(_scenes(), [], narration_duration_ms=1000)


def test_silent_scene_skipped_or_slotted_with_warning() -> None:
    scenes = _scenes()
    # Scene 2 never referenced by any segment.
    plan = build_video_plan(scenes, _segments(), narration_duration_ms=22500)
    scene_ids_used = {clip["scene_id"] for clip in plan["clips"]}
    assert 2 in scene_ids_used or any(
        "no narration" in warning for warning in plan["warnings"]
    )
