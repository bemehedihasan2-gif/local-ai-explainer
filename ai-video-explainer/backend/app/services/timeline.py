"""Timeline alignment & context aggregation (Phase 4).

Binds the per-stage evidence to scenes:

- transcript segments belong to a scene when their time ranges **overlap**
  the scene (``seg.start < scene.end and seg.end > scene.start``);
- OCR entries belong when ``scene.start <= ts <= scene.end``;
- visual metadata is matched by representative timestamp;
- the deterministic **information density** score (0-100) summarizes how
  much evidence a scene carries - later phases use it to pick scenes for
  short/long explanations. No explanation is written here.
"""

from __future__ import annotations

from typing import Any

from app.utils.logging import get_logger

logger = get_logger("app.services.timeline")

SCHEMA_VERSION = 1


def _overlaps(seg_start: float, seg_end: float, scene_start: float, scene_end: float) -> bool:
    return seg_start < scene_end and seg_end > scene_start


def _density_score(
    scene: dict[str, Any],
    speech: list[dict[str, Any]],
    ocr: list[dict[str, Any]],
    visual: dict[str, Any] | None,
) -> int:
    """Deterministic 0-100 evidence score (weights documented below)."""
    duration = max(0.1, scene["duration"])

    # Speech coverage: fraction of the scene covered by speech (0..40).
    if speech:
        covered = 0.0
        for seg in speech:
            covered += max(0.0, min(seg["end"], scene["end"]) - max(seg["start"], scene["start"]))
        speech_score = 40.0 * min(1.0, covered / duration)
    else:
        speech_score = 0.0

    # Word rate: words per minute of scene time, capped (0..20).
    words = sum(len(seg["text"].split()) for seg in speech)
    wpm = words / (duration / 60.0)
    word_score = 20.0 * min(1.0, wpm / 150.0)

    # OCR presence (0..15) and visual quality (0..25).
    ocr_score = 15.0 if ocr else 0.0
    visual_score = 0.0
    if visual:
        visual_score += 10.0 * (1.0 - float(visual.get("blur_estimate", 1.0)))
        visual_score += 10.0 * float(visual.get("complexity", 0.0))
        visual_score = min(25.0, visual_score)

    density = speech_score + word_score + ocr_score + visual_score
    return int(round(min(100.0, density)))


def build_timeline(
    *,
    scenes: list[dict[str, Any]],
    transcript: dict[str, Any] | None,
    ocr_results: list[dict[str, Any]],
    visual_results: dict[str, Any],
    duration_seconds: float,
) -> dict[str, Any]:
    """Assemble the unified per-scene evidence timeline document."""
    visual_by_ts = {
        frame["timestamp"]: frame for frame in visual_results.get("frames", [])
    }
    transcript_segments = (transcript or {}).get("segments", [])

    timeline_scenes: list[dict[str, Any]] = []
    for scene in scenes:
        s_start, s_end = scene["start"], scene["end"]
        speech = [
            seg for seg in transcript_segments
            if _overlaps(float(seg["start"]), float(seg["end"]), s_start, s_end)
        ]
        ocr = [
            entry for entry in ocr_results
            if s_start <= float(entry["timestamp"]) <= s_end
        ]
        visual = visual_by_ts.get(scene["representative_timestamp"])

        timeline_scenes.append({
            "scene_id": scene["scene_id"],
            "start": s_start,
            "end": s_end,
            "duration": scene["duration"],
            "representative_timestamp": scene["representative_timestamp"],
            "representative_frame": scene.get("representative_frame"),
            "speech_present": bool(speech),
            "speech": speech,
            "ocr_present": bool(ocr),
            "ocr": ocr,
            "visual": visual,
            "information_density": _density_score(scene, speech, ocr, visual),
        })

    summary = {
        "scene_count": len(timeline_scenes),
        "speech_scenes": sum(1 for s in timeline_scenes if s["speech_present"]),
        "ocr_scenes": sum(1 for s in timeline_scenes if s["ocr_present"]),
        "total_words": sum(
            len(seg["text"].split())
            for seg in transcript_segments
        ),
        "detected_language": (transcript or {}).get("language"),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "duration_seconds": round(float(duration_seconds), 3),
        "summary": summary,
        "scenes": timeline_scenes,
    }


def quality_check(
    *,
    scenes: list[dict[str, Any]],
    transcript: dict[str, Any] | None,
    ocr_results: list[dict[str, Any]],
    timeline: dict[str, Any],
    duration_seconds: float,
) -> list[str]:
    """Validate timestamps/ranges; returns human-readable warnings."""
    warnings: list[str] = []
    for scene in scenes:
        if not (scene["end"] > scene["start"] and scene["duration"] > 0):
            warnings.append(f"Scene {scene['scene_id']} has invalid range.")
        if scene["end"] > duration_seconds + 0.01:
            warnings.append(f"Scene {scene['scene_id']} exceeds the video duration.")
    for seg in (transcript or {}).get("segments", []):
        if not (seg["end"] > seg["start"]):
            warnings.append(f"Transcript segment {seg['id']} has invalid timestamps.")
        if seg["end"] > duration_seconds + 0.01:
            warnings.append(f"Transcript segment {seg['id']} exceeds the video duration.")
    for entry in ocr_results:
        if not (0.0 <= entry["timestamp"] <= duration_seconds + 0.01):
            warnings.append(f"OCR entry at {entry['timestamp']}s is outside the video.")
    if timeline["scenes"] and not any(s["representative_frame"] for s in timeline["scenes"]):
        warnings.append("No representative frames were extracted.")
    if transcript is not None and not transcript.get("segments"):
        warnings.append("Speech present but no transcript segments were produced.")
    return warnings


__all__ = ["build_timeline", "quality_check"]