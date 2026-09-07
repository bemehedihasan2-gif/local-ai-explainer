"""Deterministic narration quality control (Phase 6).

Runs after the narration audio, timeline and subtitles exist on disk and
verifies every artifact - nothing is assumed:

- audio: readable WAV, expected rate/channels/depth, non-zero duration,
  no silence, no clipping;
- timeline: ordered, non-negative, start < end, no overlaps;
- subtitles: valid SRT syntax, ordered non-overlapping cues, non-empty
  text, caption limits respected;
- mapping: every narration segment references script text and scene ids
  that actually exist;
- duration consistency: the assembled WAV length matches the timeline.

Score formula (documented in ``formula``): each dimension starts at 100
and loses 25 points per failed *error* check and 5 per *warning* check
(floor 0); the five dimension scores are then weighted
audio 25% / timeline 20% / subtitles 20% / mapping 20% / duration 15%
into a single 0-100 ``quality_score``. A missing or unreadable core
artifact (WAV or SRT) raises :class:`NarrationError` - QC never records
success on broken output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.narration_audio import read_wav_info, scan_levels
from app.services.subtitles import parse_srt, wrap_lines
from app.utils.errors import NarrationError
from app.utils.logging import get_logger

logger = get_logger("app.services.narration_qc")

#: Weight of each dimension in the final 0-100 score.
_WEIGHTS: dict[str, float] = {
    "audio_score": 0.25,
    "timeline_score": 0.20,
    "subtitle_score": 0.20,
    "mapping_score": 0.20,
    "duration_consistency_score": 0.15,
}

_ERROR_PENALTY = 25
_WARN_PENALTY = 5


def _clamp(score: float) -> int:
    return max(0, min(100, round(score)))


class _Dim:
    """Collector for one QC dimension's checks."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.checks: list[dict[str, Any]] = []
        self.errors = 0
        self.warnings = 0

    def check(self, name: str, passed: bool, message: str, severity: str = "warn") -> None:
        if not passed:
            if severity == "error":
                self.errors += 1
            else:
                self.warnings += 1
        self.checks.append({
            "check": f"{self.label}_{name}",
            "passed": bool(passed),
            "severity": severity,
            "message": message,
        })

    def score(self) -> int:
        return _clamp(100 - _ERROR_PENALTY * self.errors - _WARN_PENALTY * self.warnings)


def run_quality_check(
    settings: Any,
    *,
    timeline: dict[str, Any],
    segments_doc: dict[str, Any],
    script_doc: dict[str, Any],
    wav_path: Path,
    srt_path: Path,
    expected_sample_rate: int | None = None,
) -> dict[str, Any]:
    """Score the finished narration artifacts; see module docstring."""
    warnings: list[str] = []
    audio = _Dim("audio")
    timeline_dim = _Dim("timeline")
    subtitle = _Dim("subtitle")
    mapping = _Dim("mapping")
    duration_dim = _Dim("duration")

    # ---- Audio ----------------------------------------------------------
    try:
        wav_info = read_wav_info(wav_path)
    except Exception as exc:  # noqa: BLE001 - reported as failed dimension
        raise NarrationError(f"QC could not read the narration WAV: {exc}") from exc

    audio.check("exists", wav_info["duration_ms"] > 0, "WAV exists and is non-empty", "error")
    audio.check(
        "duration_positive", wav_info["duration_ms"] > 0,
        f"WAV duration is {wav_info['duration_ms']} ms", "error",
    )
    audio.check(
        "sample_rate", expected_sample_rate is None or wav_info["sample_rate"] == expected_sample_rate,
        f"Sample rate {wav_info['sample_rate']} Hz"
        + (f" (expected {expected_sample_rate} Hz)" if expected_sample_rate else ""),
        "error",
    )
    audio.check("mono", wav_info["channels"] == 1, f"Channels: {wav_info['channels']}", "error")
    audio.check("s16", wav_info["sampwidth"] == 2, f"Bit depth: {wav_info['sampwidth'] * 8}-bit", "error")

    try:
        levels = scan_levels(wav_path)
    except Exception as exc:  # noqa: BLE001
        raise NarrationError(f"QC could not scan the narration WAV: {exc}") from exc
    silence_threshold = getattr(settings, "audio_qc_silence_threshold_db", -60.0)
    audio.check(
        "not_silent", levels["mean_db"] > silence_threshold,
        f"Mean loudness {levels['mean_db']} dBFS (threshold {silence_threshold} dBFS)",
        "error",
    )
    audio.check(
        "no_clipping", levels["clip_ratio"] == 0,
        f"Clipped samples: {levels['clipped_samples']} "
        f"({levels['clip_ratio'] * 100:.4f}%)",
    )
    audio.check(
        "no_hard_clipping", levels["peak_db"] <= -0.01,
        f"Peak {levels['peak_db']} dBFS (values > -0.01 dBFS are hard clipping)",
        "error",
    )

    # ---- Timeline ---------------------------------------------------------
    segments = timeline.get("segments") or []
    total_ms = 0
    prev_end = -1
    timeline_dim.check("has_segments", len(segments) > 0, f"{len(segments)} segments", "error")
    for segment in segments:
        start_ms = int(segment.get("start_ms", -1))
        end_ms = int(segment.get("end_ms", -1))
        if start_ms < 0:
            timeline_dim.check("no_negative_start", False, f"segment {segment.get('segment_id')} start < 0", "error")
        else:
            timeline_dim.check("no_negative_start", True, "", "error")
        if end_ms <= start_ms:
            timeline_dim.check("start_before_end", False, f"segment {segment.get('segment_id')}: {start_ms} -> {end_ms}", "error")
        else:
            timeline_dim.check("start_before_end", True, "", "error")
        if prev_end >= 0 and start_ms < prev_end:
            timeline_dim.check("no_overlap", False, f"segment {segment.get('segment_id')} overlaps previous", "error")
        else:
            timeline_dim.check("no_overlap", True, "", "error")
        total_ms = max(total_ms, end_ms)
        prev_end = end_ms
    ordered = all(
        int(segments[i].get("end_ms", 0)) <= int(segments[i + 1].get("start_ms", 0))
        for i in range(len(segments) - 1)
    )
    timeline_dim.check("ordered", ordered, "Segments are in chronological order", "error")
    if not segments:
        total_ms = 0

    # ---- Duration consistency ------------------------------------------------
    tolerance = getattr(settings, "narration_qc_duration_tolerance_ms", 400)
    diff = abs(wav_info["duration_ms"] - total_ms)
    duration_dim.check(
        "audio_matches_timeline", diff <= tolerance,
        f"WAV {wav_info['duration_ms']} ms vs timeline {total_ms} ms (Δ {diff} ms, tolerance {tolerance} ms)",
        "error",
    )

    # ---- Subtitles ------------------------------------------------------------
    try:
        srt_text = srt_path.read_text(encoding="utf-8")
        cues = parse_srt(srt_text)
    except NarrationError as exc:
        raise NarrationError(f"QC rejected the subtitles: {exc}") from exc
    subtitle.check("nonempty", len(cues) > 0, f"{len(cues)} cues parsed", "error")
    for index, cue in enumerate(cues):
        if cue["end_ms"] <= cue["start_ms"]:
            subtitle.check(
                "cue_start_before_end", False,
                f"cue {index + 1}: {cue['start_ms']} -> {cue['end_ms']}", "error",
            )
        if index > 0 and cue["start_ms"] < cues[index - 1]["end_ms"]:
            subtitle.check(
                "cue_no_overlap", False,
                f"cue {index + 1} overlaps cue {index}", "error",
            )
    max_caption = getattr(settings, "subtitle_max_chars_per_caption", 84)
    max_line = getattr(settings, "subtitle_max_chars_per_line", 42)
    long_cues = sum(
        1 for cue in cues
        if len(cue["text"]) > max_caption
        or any(len(line) > max_line for line in wrap_lines(cue["text"], max_line))
    )
    subtitle.check(
        "caption_limits", long_cues == 0,
        f"{long_cues} caption(s) exceed the configured limits",
    )
    empty_cues = sum(1 for cue in cues if not cue["text"].strip())
    subtitle.check("no_empty_text", empty_cues == 0, f"{empty_cues} empty cue(s)", "error")
    # every timeline segment is covered by at least one cue
    timeline_covers = {int(segment.get("start_ms", -1)) for segment in segments}
    cue_starts = {cue["start_ms"] for cue in cues}
    subtitle.check(
        "timeline_covered",
        (not timeline_covers) or timeline_covers <= cue_starts,
        "All timeline segments have a starting subtitle cue",
        "error",
    )

    # ---- Mapping ---------------------------------------------------------------
    seg_by_id = {int(s["segment_id"]): s for s in segments_doc.get("segments", [])}
    allowed_scene_ids = {
        int(sid)
        for section in (script_doc.get("sections") or [])
        for sid in (section.get("scene_ids") or [])
    }
    missing = 0
    wrong_text = 0
    bad_scene = 0
    for segment in segments:
        sid = int(segment.get("segment_id", -1))
        source = seg_by_id.get(sid)
        if source is None:
            missing += 1
            continue
        if (source.get("text") or "").strip() != (segment.get("text") or "").strip():
            wrong_text += 1
        scene_ids = [int(s) for s in (segment.get("scene_ids") or [])]
        if scene_ids and allowed_scene_ids and not set(scene_ids) <= allowed_scene_ids:
            bad_scene += 1
    mapping.check("all_segments_present", missing == 0, f"{missing} missing segment(s)", "error")
    mapping.check("text_consistent", wrong_text == 0, f"{wrong_text} text mismatch(es)", "error")
    mapping.check("scene_ids_valid", bad_scene == 0, f"{bad_scene} invalid scene reference(s)", "error")

    # ---- Score -------------------------------------------------------------
    dims = {
        "audio_score": audio.score(),
        "timeline_score": timeline_dim.score(),
        "subtitle_score": subtitle.score(),
        "mapping_score": mapping.score(),
        "duration_consistency_score": duration_dim.score(),
    }
    overall = sum(dims[name] * weight for name, weight in _WEIGHTS.items())
    if not segments:
        warnings.append("The narration timeline is empty - subtitles and QC are degraded.")
    if dims["audio_score"] < 100:
        warnings.append("Narration audio quality is below the perfect score (see checks).")
    if dims["subtitle_score"] < 100:
        warnings.append("Subtitle quality is below the perfect score (see checks).")

    checks = (
        audio.checks + timeline_dim.checks + subtitle.checks
        + mapping.checks + duration_dim.checks
    )
    result = {
        "schema_version": 1,
        "quality_score": _clamp(overall),
        "scores": dims,
        "formula": {
            "weights": _WEIGHTS,
            "error_penalty": _ERROR_PENALTY,
            "warning_penalty": _WARN_PENALTY,
            "note": (
                "Each dimension starts at 100 and loses 25 per failed error "
                "check and 5 per warning; dimensions are weighted "
                "audio 25% / timeline 20% / subtitles 20% / mapping 20% / "
                "duration 15%."
            ),
        },
        "checks": checks,
        "warnings": warnings,
        "audio": {
            "duration_ms": wav_info["duration_ms"],
            "sample_rate": wav_info["sample_rate"],
            "channels": wav_info["channels"],
            "peak_db": levels["peak_db"],
            "mean_db": levels["mean_db"],
        },
        "timeline_total_ms": total_ms,
        "segment_count": len(segments),
        "cue_count": len(cues),
    }
    logger.info(
        "Narration QC: score=%d/%d (audio=%d, timeline=%d, subtitles=%d, mapping=%d).",
        result["quality_score"], 100, dims["audio_score"], dims["timeline_score"],
        dims["subtitle_score"], dims["mapping_score"],
    )
    return result


__all__ = ["run_quality_check"]
