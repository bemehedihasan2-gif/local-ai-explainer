"""Speech-to-text (Phase 4): faster-whisper, CPU-only, lazy-loaded.

Model lifecycle (8 GB RAM policy):

- The model is **lazy-loaded** when transcription starts, never at app
  startup, and the returned engine is released by the caller afterwards.
- The model is **never silently downloaded**. If the local model folder
  (``models/whisper/<WHISPER_MODEL>/``) is missing, transcription raises
  :class:`WhisperModelMissingError` (``model_download_required``) and the
  analysis job continues without speech - the README documents the explicit
  one-time download command (no API key needed).

Language handling (``WHISPER_LANGUAGE_MODE``):

- ``preferred`` (default): auto-detect, but the project's UI language is
  recorded as the *expected* language for downstream context. Whisper is
  never forced to match it when the audio is actually different.
- ``auto``: pure auto-detection.
- ``forced``: force the project language (``language=<code>``) - only for
  users who know the audio language matches the UI language.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Callable

from app.ai.base import PipelineService
from app.models.enums import PipelineStage
from app.utils.errors import (
    WhisperModelMissingError,
    WhisperUnavailableError,
)
from app.utils.logging import get_logger

logger = get_logger("app.ai.stt")

#: Whisper language codes for the languages this app offers.
_LANGUAGE_CODES = {"en": "en", "hi": "hi", "bn": "bn"}

#: Files that prove a faster-whisper model snapshot is present locally.
_MODEL_PROOF_FILES = ("model.bin", "config.json")


def whisper_package_installed() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def whisper_model_dir(settings: Any) -> Path:
    """``models/whisper/<model>`` relative to the configured model dir."""
    return Path(settings.model_dir) / "whisper" / settings.whisper_model


def whisper_model_installed(settings: Any) -> bool:
    """True only when the local snapshot actually exists (no download check)."""
    folder = whisper_model_dir(settings)
    if not folder.is_dir():
        return False
    return any((folder / name).is_file() for name in _MODEL_PROOF_FILES)


def _whisper_download_hint(settings: Any) -> str:
    """Install instructions; never includes absolute filesystem paths
    (warnings are surfaced through the public API)."""
    model = settings.whisper_model
    # Relative to the project root's models/ directory - no machine paths.
    rel_target = f"models/whisper/{model}"
    return (
        f"The faster-whisper '{model}' model is not installed. One-time, "
        "explicit download (no API key):\n"
        f"    python -m pip install huggingface_hub\n"
        f"    python -c \"from huggingface_hub import snapshot_download; "
        f"snapshot_download(repo_id='Systran/faster-whisper-{model}', "
        f"local_dir='{rel_target}')\"\n"
        f"(run from the project root so the files land in "
        f"models/whisper/{model}/), or use scripts\\download_whisper_model.bat "
        "on Windows. Analysis continues without speech recognition until "
        "then."
    )


class SpeechToTextService(PipelineService):
    stage = PipelineStage.SPEECH_TO_TEXT
    name = "Speech-to-Text"
    planned_for = "Phase 4"

    def __init__(self, settings: Any | None = None) -> None:
        super().__init__(settings)
        self._model = None  # lazy; released via release_model()

    # -- availability ---------------------------------------------------
    def availability(self) -> dict[str, object]:
        """Honest dependency report for /api/system/status."""
        installed = whisper_package_installed()
        model_ready = installed and whisper_model_installed(self.settings)
        return {
            "package": "installed" if installed else "not_installed",
            "model": "ready" if model_ready else "not_installed",
            "model_name": self.settings.whisper_model if self.settings else None,
            "device": self.settings.whisper_device if self.settings else None,
            "compute_type": self.settings.whisper_compute_type if self.settings else None,
            "model_dir": str(whisper_model_dir(self.settings)) if self.settings else None,
            "download_required": installed and not model_ready,
        }

    # -- model lifecycle -------------------------------------------------
    def _load_model(self) -> Any:
        """Import + instantiate faster-whisper lazily; cache the engine."""
        if self._model is not None:
            return self._model
        if not whisper_package_installed():
            raise WhisperUnavailableError()
        model_path = whisper_model_dir(self.settings)
        if not whisper_model_installed(self.settings):
            raise WhisperModelMissingError(_whisper_download_hint(self.settings))
        from faster_whisper import WhisperModel  # deferred: heavy import

        logger.info(
            "Loading Whisper '%s' (device=%s, compute=%s)…",
            self.settings.whisper_model, self.settings.whisper_device,
            self.settings.whisper_compute_type,
        )
        self._model = WhisperModel(
            str(model_path),
            device=self.settings.whisper_device,
            compute_type=self.settings.whisper_compute_type,
            cpu_threads=2,  # leave cores for the rest of the system
        )
        return self._model

    def release_model(self) -> None:
        """Unload the engine so RAM returns before the next heavy stage."""
        self._model = None

    # -- transcription ----------------------------------------------------
    def transcribe(
        self,
        wav_path: str | Path,
        *,
        preferred_language: str | None,
        duration_seconds: float,
        progress_callback: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        """Transcribe a 16 kHz mono WAV; returns the transcript document.

        Result::

            {
              "schema_version": 1,
              "language": <detected or forced code>,
              "language_probability": <float or None>,
              "duration_seconds": <float>,
              "segments": [{"id", "start", "end", "text", "confidence"}],
              "warnings": [...],
            }
        """
        mode = (self.settings.whisper_language_mode if self.settings else "preferred")
        language = None
        if mode == "forced":
            language = _LANGUAGE_CODES.get(preferred_language or "")
        elif mode == "preferred" and preferred_language:
            # Hint only: faster-whisper auto-detects when language=None, so
            # 'preferred' passes None and lets detection decide (honest).
            logger.info(
                "Whisper language mode='preferred' (project language %s used "
                "as a hint; auto-detection stays on).",
                preferred_language,
            )
            language = None

        model = self._load_model()
        segments_iter, info = model.transcribe(
            str(wav_path),
            language=language,
            beam_size=5,
            vad_filter=True,  # skips silence cheaply on CPU
        )
        detected = info.language if language is None else language
        probability = float(info.language_probability) if language is None else None
        if language is not None and detected is None:
            detected = language

        segments: list[dict[str, Any]] = []
        for segment in segments_iter:  # generator: never all in RAM at once
            segments.append({
                "id": segment.id,
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": segment.text.strip(),
                "confidence": round(float(segment.avg_logprob), 3),
            })
            if progress_callback is not None and duration_seconds > 0:
                progress_callback(min(1.0, segment.end / duration_seconds))

        transcript = {
            "schema_version": 1,
            "language": detected,
            "language_probability": probability,
            "duration_seconds": round(float(duration_seconds), 3),
            "segments": segments,
            "warnings": [],
        }
        logger.info(
            "Transcribed %s: %d segment(s), language=%s (p=%.2f)",
            Path(wav_path).name, len(segments), detected, probability or 0.0,
        )
        return transcript


__all__ = [
    "SpeechToTextService",
    "whisper_package_installed",
    "whisper_model_installed",
    "whisper_model_dir",
]