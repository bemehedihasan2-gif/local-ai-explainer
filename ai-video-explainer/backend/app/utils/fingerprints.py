"""Deterministic fingerprints (Phase 4 idempotency).

- ``config_fingerprint``: hashes every analysis-relevant setting, so
  changing e.g. SCENE_THRESHOLD or WHISPER_MODEL invalidates past results.
- ``preprocessing_fingerprint``: hashes what the analysis actually consumes
  (the uploaded file's SHA-256 + the analysis copy's recorded specs), so a
  re-uploaded/re-preprocessed video never reuses stale analysis.

Both are plain hex strings stored on ``analysis_results`` rows.
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


def _sha256_json(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["analysis_config_fingerprint", "preprocessing_fingerprint"]