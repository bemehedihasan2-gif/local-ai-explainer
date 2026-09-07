"""Deterministic fingerprints (Phase 4-6 idempotency).

- ``analysis_config_fingerprint``: hashes every analysis-relevant setting,
  so changing e.g. SCENE_THRESHOLD or WHISPER_MODEL invalidates past results.
- ``preprocessing_fingerprint``: hashes what the analysis actually consumes
  (the uploaded file's SHA-256 + the analysis copy's recorded specs), so a
  re-uploaded/re-preprocessed video never reuses stale analysis.
- ``story_generation_fingerprint``: hashes everything that changes Phase 5
  story/script output (analysis identity + language + duration + model and
  planner configuration).
- ``narration_fingerprint``: hashes everything that changes Phase 6 narration
  output (script identity + language + voice + TTS/audio/subtitle settings).

Fingerprints are plain hex strings stored on the run rows.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_ANALYSIS_SETTINGS = (
    "whisper_model",
    "whisper_device",
    "whisper_compute_type",
    "whisper_language_mode",
    "scene_threshold",
    "min_scene_duration_seconds",
    "max_scenes",
    "ocr_enabled",
    "ocr_frame_limit",
    "visual_analysis_enabled",
    "analysis_width",
    "analysis_fps",
)


def analysis_config_fingerprint(settings: Any) -> str:
    """Hash of the settings that change analysis output."""
    payload = {
        name: getattr(settings, name) for name in _ANALYSIS_SETTINGS
    }
    return _sha256_json(payload)


def preprocessing_fingerprint(project_row: dict[str, Any]) -> str:
    """Hash of the source + analysis-copy specs this run consumes."""
    payload = {
        "source_sha256": project_row.get("sha256"),
        "duration": project_row.get("duration"),
        "analysis_width": project_row.get("analysis_width"),
        "analysis_height": project_row.get("analysis_height"),
        "analysis_fps": project_row.get("analysis_fps"),
    }
    return _sha256_json(payload)


_STORY_SETTINGS = (
    "llm_provider",
    "llama_threads",
    "llama_context_size",
    "llama_max_tokens",
    "llm_temperature",
    "llm_seed",
    "story_batch_scenes",
    "story_max_excerpt_chars",
    "story_prompt_version",
    "script_prompt_version",
    "planner_version",
    "narration_wpm",
    "script_word_targets",
    "importance_weight_information_density",
    "importance_weight_speech_density",
    "importance_weight_semantic",
    "importance_weight_turning_point",
    "importance_weight_continuity",
    "importance_weight_ocr",
    "importance_redundancy_penalty",
    "min_words_per_scene",
    "max_words_per_scene",
    "min_scenes_per_script",
    "max_selected_scenes",
)


def story_generation_fingerprint(
    settings: Any,
    project_row: dict[str, Any],
    *,
    language: str,
    target_duration_seconds: int,
) -> str:
    """Hash of everything that changes Phase 5 output.

    Combines the consumed analysis identity (config + preprocessing
    fingerprints) with the generation options (language, duration) and the
    model/planner configuration. Stored on ``script_runs`` rows; when it
    matches a completed run, results are reused instead of regenerated.
    """
    from app.ai.llm import llm_model_identity

    payload: dict[str, Any] = {
        "analysis_config": analysis_config_fingerprint(settings),
        "preprocessing": preprocessing_fingerprint(project_row),
        "language": language,
        "target_duration_seconds": target_duration_seconds,
        "llm_model": llm_model_identity(settings),
    }
    for name in _STORY_SETTINGS:
        payload[name] = getattr(settings, name)
    return _sha256_json(payload)


_TTS_SETTINGS = (
    "tts_provider",
    "tts_sample_rate",
    "tts_channels",
    "tts_speaker",
    "tts_length_scale",
    "tts_gap_ms_within_section",
    "tts_gap_ms_between_sections",
    "tts_lead_in_ms",
    "narration_max_segment_chars",
    "narration_max_segment_words",
    "subtitle_max_chars_per_line",
    "subtitle_max_chars_per_caption",
    "audio_target_mean_db",
    "audio_max_gain_db",
    "audio_peak_ceiling_db",
    "narration_qc_duration_tolerance_ms",
)


_RENDER_SETTINGS = (
    "output_max_width",
    "output_max_height",
    "output_fps",
    "video_codec",
    "video_preset",
    "video_crf",
    "original_audio_enabled",
    "original_audio_volume",
    "narration_audio_volume",
    "audio_ducking_enabled",
    "audio_ducking_threshold",
    "audio_ducking_ratio",
    "audio_ducking_attack_ms",
    "audio_ducking_release_ms",
    "subtitle_burn_enabled",
    "subtitle_font_path",
    "subtitle_font_name",
    "subtitle_font_size",
    "subtitle_margin_v",
    "render_tail_ms",
    "renderer_version",
)


def render_fingerprint(
    settings: Any,
    *,
    source_sha256: str | None,
    source_duration: float | None,
    selected_scenes_digest: str,
    narration_fingerprint: str,
) -> str:
    """Hash of everything that changes the Phase 7 final render.

    Combines the consumed media identity (source hash + duration), the
    Phase 5 scene-selection document and the Phase 6 narration fingerprint
    with every render/subtitle/audio setting. Stored on ``render_runs``;
    when it matches a completed run with a valid final.mp4 the render is
    reused instead of being encoded again.
    """
    payload: dict[str, Any] = {
        "source_sha256": source_sha256,
        "source_duration": source_duration,
        "selected_scenes": selected_scenes_digest,
        "narration_fingerprint": narration_fingerprint,
    }
    for name in _RENDER_SETTINGS:
        payload[name] = getattr(settings, name)
    return _sha256_json(payload)


def narration_fingerprint(
    settings: Any,
    script_fingerprint: str,
    *,
    language: str,
    voice_id: str | None,
) -> str:
    """Hash of everything that changes Phase 6 narration output.

    Combines the Phase 5 script identity (its generation fingerprint),
    language, the resolved voice model basename and every TTS/subtitle/
    audio setting. Stored on ``tts_runs`` rows; when it matches a completed
    run whose artifacts are present, the narration is reused instead of
    being synthesized again.
    """
    payload: dict[str, Any] = {
        "script_fingerprint": script_fingerprint,
        "language": language,
        "voice_id": voice_id,
        "tts_provider": getattr(settings, "tts_provider", "piper"),
    }
    for name in _TTS_SETTINGS:
        payload[name] = getattr(settings, name)
    return _sha256_json(payload)


def _sha256_json(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "analysis_config_fingerprint",
    "preprocessing_fingerprint",
    "story_generation_fingerprint",
    "narration_fingerprint",
    "render_fingerprint",
]
