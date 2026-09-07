"""Phase 7 render planning (pure, deterministic).

Turns the Phase 5 ``selected_scenes.json`` + Phase 6 narration timeline into
a concrete output plan: which source ranges play, for how long, in which
order, and where the (absolute) narration/subtitle timeline sits on top.

Design rules (documented, see docs/architecture.md):
- The **narration timeline is the master clock**: every narration segment
  starts at exactly the same absolute millisecond in the output as it does
  in ``narration_timeline.json``, so subtitles and the voice track never
  drift from the visuals. The video is never cut shorter than the narration.
- Narration segments are assigned to the earliest still-unassigned selected
  scene whose id they reference (chronological, forward-only). The assigned
  run of segments for one scene forms its display window.
- When a scene's source range is shorter than its window the last frame is
  held (``tpad clone``); when a window leaves a narration gap the previous
  scene is held into the gap. Gaps larger than ``hold_gap_max_ms`` and the
  final tail are also handled as holds - no black frames, no dead air.
- Every selected scene keeps a real (possibly trimmed) source clip; source
  scenes are never reordered (story order == file order == chronology).

Nothing here touches FFmpeg; it only produces JSON-safe plans.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.utils.errors import RenderArtifactError
from app.utils.logging import get_logger

logger = get_logger("app.services.render_plan")

SCHEMA_VERSION = 1


def _ms(value: Any) -> int:
    """Accept seconds (float) or ms (int) values and return ms."""
    number = float(value)
    if number < 1000:  # seconds -> ms
        number *= 1000
    return int(round(number))


def _scene_span(scene: dict[str, Any]) -> tuple[int, int]:
    """(source_start_ms, source_end_ms) for a selected-scene row."""
    if "source_start_ms" in scene and "source_end_ms" in scene:
        start, end = int(scene["source_start_ms"]), int(scene["source_end_ms"])
    else:
        start, end = _ms(scene.get("start", 0)), _ms(scene.get("end", 0))
    if end <= start:
        raise RenderArtifactError(
            f"Selected scene {scene.get('scene_id')} has an invalid source "
            "range (end <= start)."
        )
    return start, end


def build_video_plan(
    selected_scenes: list[dict[str, Any]],
    narration_segments: list[dict[str, Any]],
    *,
    narration_duration_ms: int,
    tail_ms: int = 1200,
    hold_gap_max_ms: int = 1500,
) -> dict[str, Any]:
    """Build the output clip timeline (see module docstring)."""
    if not selected_scenes:
        raise RenderArtifactError("No selected scenes were found - re-run Phase 5.")
    if not narration_segments:
        raise RenderArtifactError("No narration segments were found - re-run Phase 6.")

    scenes: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for scene in selected_scenes:
        scene_id = int(scene["scene_id"])
        if scene_id in seen_ids:
            continue  # defensive: tolerate duplicated rows
        seen_ids.add(scene_id)
        start_ms, end_ms = _scene_span(scene)
        scenes.append({
            "scene_id": scene_id,
            "source_start_ms": start_ms,
            "source_end_ms": end_ms,
            "source_duration_ms": end_ms - start_ms,
            "importance_score": float(scene.get("importance_score", 0.0)),
        })

    segments: list[dict[str, Any]] = []
    for segment in narration_segments:
        seg_id = int(segment["segment_id"])
        segment_ms = (
            int(segment["start_ms"]) if "start_ms" in segment
            else _ms(segment.get("start"))
        )
        end_ms = (
            int(segment["end_ms"]) if "end_ms" in segment
            else _ms(segment.get("end"))
        )
        if end_ms <= segment_ms:
            raise RenderArtifactError(
                f"Narration segment {seg_id} has an invalid window."
            )
        scene_ids = [int(s) for s in segment.get("scene_ids", [])]
        segments.append({
            "segment_id": seg_id,
            "start_ms": segment_ms,
            "end_ms": end_ms,
            "scene_ids": scene_ids,
            "text": segment.get("text", ""),
            "section": segment.get("section", ""),
        })

    by_scene = {scene["scene_id"]: scene for scene in scenes}
    # Greedy, forward-only assignment of narration segments to scenes.
    scene_index = 0
    assigned: list[list[int]] = [[] for _ in scenes]  # segment ids per scene
    unassigned: list[dict[str, Any]] = []
    scene_order_ids = [scene["scene_id"] for scene in scenes]
    for segment in segments:
        match = None
        # The earliest selected scene at/after the current pointer that the
        # segment references.
        for index in range(scene_index, len(scenes)):
            if scenes[index]["scene_id"] in segment["scene_ids"]:
                match = index
                break
        if match is None:
            # A segment can only reference *later* content (it should not,
            # per Phase 5 chronology checks, but stay robust): keep the
            # pointer fixed and let a later scene claim it.
            unassigned.append(segment)
            continue
        scene_index = max(scene_index, match)  # never rewind chronology
        assigned[match].append(segment["segment_id"])

    # Build scene windows from their assigned segments.
    seg_by_id = {segment["segment_id"]: segment for segment in segments}
    windows: list[dict[str, Any]] = []
    for scene, seg_ids in zip(scenes, assigned):
        ids = list(seg_ids)
        ids.extend(seg["segment_id"] for seg in unassigned
                   if scene["scene_id"] in seg["scene_ids"])
        if not ids:
            windows.append({**scene, "window_ms": None, "segments": []})
            continue
        window_start = min(seg_by_id[i]["start_ms"] for i in ids)
        window_end = max(seg_by_id[i]["end_ms"] for i in ids)
        windows.append({
            **scene,
            "window_ms": [window_start, window_end],
            "segments": sorted(ids),
        })

    # Order windows by narration start (scenes without narration go last in
    # their file order and are slotted into narration gaps below).
    voiced = [w for w in windows if w["window_ms"] is not None]
    silent = [w for w in windows if w["window_ms"] is None]
    voiced.sort(key=lambda w: (w["window_ms"][0], w["scene_id"]))
    # Chronology guard: voiced windows must not move backwards; if the sort
    # ever disagrees with the file order the file order wins (story plan).
    file_order = {scene["scene_id"]: i for i, scene in enumerate(scenes)}
    voiced.sort(key=lambda w: (file_order[w["scene_id"]], w["window_ms"][0]))

    clips: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    cursor_ms = 0
    warnings: list[str] = []
    held_silent = list(silent)

    def add_source_clip(
        scene: dict[str, Any],
        *,
        output_start_ms: int,
        duration_ms: int,
        max_source_ms: int,
    ) -> dict[str, Any]:
        """Slice ``scene`` for ``duration_ms`` of output starting at its
        source start (truncated to ``max_source_ms``); the caller pads the
        remainder via hold."""
        length = max(1, min(duration_ms, max_source_ms, scene["source_duration_ms"]))
        hold = max(0, duration_ms - length)
        clip = {
            "mode": "source",
            "scene_id": scene["scene_id"],
            "source_start_ms": scene["source_start_ms"],
            "source_end_ms": scene["source_start_ms"] + length,
            "output_start_ms": output_start_ms,
            "output_end_ms": output_start_ms + length + hold,
            "hold_ms": hold,
            "importance_score": scene["importance_score"],
        }
        clips.append(clip)
        return clip

    for window in voiced:
        w_start, w_end = window["window_ms"]
        window_len = w_end - w_start
        # Lead-in / gap before this window: hold the previous scene into the
        # gap. For the very first window the lead-in pre-rolls the scene.
        covered = False
        if w_start > cursor_ms:
            gap = w_start - cursor_ms
            if gap > hold_gap_max_ms:
                warnings.append(
                    f"A {gap} ms gap before scene {window['scene_id']} exceeds "
                    f"hold_gap_max_ms ({hold_gap_max_ms}); holding the previous "
                    "scene anyway (no black frames)."
                )
            if clips:
                # Extend the previous clip by the gap via its hold_ms.
                clips[-1]["hold_ms"] += gap
                clips[-1]["output_end_ms"] += gap
            else:
                # Pre-roll: start the first scene at output 0 and let it
                # play (or hold) through its whole window.
                add_source_clip(
                    window, output_start_ms=cursor_ms,
                    duration_ms=window_len + gap, max_source_ms=window_len + gap,
                )
                covered = True
            cursor_ms = w_start
        group = {
            "group_id": len(groups) + 1,
            "scene_ids": [window["scene_id"]],
            "narration_segment_ids": window["segments"],
            "subtitle_ids": list(window["segments"]),
            "output_start_ms": w_start,
            "output_end_ms": w_end,
            "clips": [],
        }
        # One source clip; source shorter than window -> hold frames.
        if not covered:
            add_source_clip(
                window, output_start_ms=w_start,
                duration_ms=window_len, max_source_ms=window_len,
            )
        group["clips"].append(clips[-1])
        groups.append(group)
        cursor_ms = max(cursor_ms, clips[-1]["output_end_ms"])

    # Slot silent (unvoiced) scenes into narration gaps when a real gap
    # exists; otherwise drop them with a warning (they carry no narration,
    # and the pipeline must not pad the video with unexplained content).
    gap_slots: list[tuple[int, int]] = []
    for index, window in enumerate(voiced):
        start = window["window_ms"][0]
        end = window["window_ms"][1]
        if index == 0:
            gap_slots.append((0, start))
        else:
            gap_slots.append((voiced[index - 1]["window_ms"][1], start))
    gap_slots.append((voiced[-1]["window_ms"][1], voiced[-1]["window_ms"][1] + tail_ms))
    for window in held_silent:
        for (g_start, g_end) in gap_slots:
            if g_end - g_start >= 2000:
                add_source_clip(
                    window, output_start_ms=g_start,
                    duration_ms=min(window["source_duration_ms"], g_end - g_start),
                    max_source_ms=g_end - g_start,
                )
                warnings.append(
                    f"Scene {window['scene_id']} has no narration; shown briefly "
                    "in a narration gap."
                )
                break
        else:
            warnings.append(
                f"Scene {window['scene_id']} has no narration and no gap to "
                "show it in; skipped by the renderer."
            )

    # Tail: hold the last clip for tail_ms after the narration ends.
    if clips:
        clips[-1]["hold_ms"] += max(0, tail_ms)
        clips[-1]["output_end_ms"] += max(0, tail_ms)
    total_ms = clips[-1]["output_end_ms"] if clips else narration_duration_ms

    if total_ms < narration_duration_ms:
        raise RenderArtifactError(
            "The planned video would end before the narration ends - render "
            "planning failed to cover the narration timeline."
        )

    plan = {
        "schema_version": SCHEMA_VERSION,
        "master_clock": "narration_absolute",
        "narration_duration_ms": int(narration_duration_ms),
        "video_duration_ms": int(total_ms),
        "tail_ms": int(tail_ms),
        "subtitle_aligned": True,  # output timeline == narration timeline
        "scene_count": len(voiced) + len(held_silent),
        "groups": groups,
        "clips": clips,
        "warnings": warnings,
    }
    return plan


def digest_document(document: dict[str, Any]) -> str:
    """Fingerprint of a Phase 5/7 JSON document (selection changes force a
    re-plan/render)."""
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_fingerprint(plan: dict[str, Any]) -> str:
    return digest_document({k: plan[k] for k in ("clips", "groups")})


__all__ = ["build_video_plan", "digest_document", "plan_fingerprint"]
