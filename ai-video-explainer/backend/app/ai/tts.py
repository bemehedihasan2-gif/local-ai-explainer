"""Local text-to-speech provider abstraction (Phase 6).

The narration pipeline never depends on one Python binding and never keeps
a model resident. :class:`PiperTTSProvider` shells out to the Piper CLI for
**every narration segment**:

- no ``shell=True``, no persistent process (one voice in RAM at a time,
  released after each subprocess);
- hard timeout that kills the subprocess;
- stdout/stderr captured and reported in useful error messages;
- voices are **never silently downloaded** - a missing executable or voice
  model is reported with explicit setup instructions;
- configured voice/model paths are never exposed through API responses
  (``describe()`` only reports the model file's basename).

``LocalTTSProvider`` is the small Protocol every future provider (e.g. a
Python binding like Coqui/piper-python, an espeak fallback) must implement.
``synthesize`` writes a WAV file and returns measured audio facts (sample
rate / channels / duration) so the pipeline never guesses timing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.ai.base import PipelineService
from app.models.enums import PipelineStage
from app.utils.errors import (
    TTSAudioError,
    TTSEngineUnavailableError,
    TTSGenerationError,
    TTSTimeoutError,
    TTSVoiceMissingError,
)
from app.utils.logging import get_logger

logger = get_logger("app.ai.tts")


class TextToSpeechService(PipelineService):
    """Registry marker for the TTS stage.

    The Phase 1 rule is preserved: ``execute()`` (the base implementation)
    still refuses to fake work. Phase 6 narration runs through the single
    worker via :class:`app.services.narration.NarrationService`, which
    drives a :class:`LocalTTSProvider` segment by segment.
    """

    stage = PipelineStage.TEXT_TO_SPEECH
    name = "Text-to-Speech"
    planned_for = "Phase 6"


#: Supported narration languages (ISO 639-1) -> settings field for the voice.
_LANGUAGE_FIELDS = {"en": "tts_voice_en", "hi": "tts_voice_hi", "bn": "tts_voice_bn"}
_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "bn": "Bengali"}

#: Piper CLI binary name(s), resolved on PATH when no path is configured.
_PIPER_NAMES = ("piper",)


def _engine_error_text() -> str:
    return (
        "The Piper CLI ('piper') was not found. Install Piper (Windows: the "
        "official 'piper' release zip from the rhasspy/piper GitHub page, or "
        "'pip install piper-tts' and use its CLI) and ensure 'piper' is on "
        "PATH, or set TTS_EXECUTABLE_PATH in .env to the binary. The app "
        "never downloads it automatically."
    )


def _voice_error_text(language: str, field_name: str, missing: bool) -> str:
    label = _LANGUAGE_NAMES.get(language, language)
    if missing:
        return (
            f"No {label} voice is configured. Set {field_name.upper()} in "
            ".env to a local Piper .onnx voice model for {label} (a sibling "
            ".onnx.json config next to it is picked up automatically). The "
            "app never downloads voices - run the explicit setup from the "
            "README ('Phase 6 - first-run TTS setup')."
        )
    return (
        f"The configured {label} voice file (TTS_VOICE_{language.upper()}) "
        "was not found on disk. Check the path in .env and re-run the "
        "explicit voice setup from the README. No automatic download is "
        "performed."
    )


class UnavailableProvider:
    """Provider returned when the TTS stage is disabled by configuration."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def available(self) -> bool:
        return False

    def synthesize(
        self,
        text: str,
        language: str,
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        raise TTSEngineUnavailableError(self._reason)

    def describe(self) -> dict[str, Any]:
        return {
            "provider": "none",
            "available": False,
            "executable_available": False,
            "languages": {
                code: {
                    "voice_id": None,
                    "available": False,
                    "configured": False,
                    "model_available": False,
                    "sample_rate": None,
                }
                for code in _LANGUAGE_FIELDS
            },
            "voices": [],
            "setup_hint": self._reason,
        }


@runtime_checkable
class LocalTTSProvider(Protocol):
    """Minimal contract every local TTS backend implements."""

    def available(self) -> bool:
        """True when the executable AND at least one voice are present."""
        ...

    def describe(self) -> dict[str, Any]:
        """Public capability report (never exposes full filesystem paths)."""
        ...

    def synthesize(
        self,
        text: str,
        language: str,
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        """Synthesize ``text`` in ``language`` to a WAV file.

        Returns measured audio facts: ``{sample_rate, channels, sampwidth,
        duration_ms, path, provider, language, voice_id}``. Raises the
        dedicated TTS errors on any failure - audio is never faked.
        """
        ...


def _wav_facts(path: Path) -> dict[str, Any]:
    """Read a WAV header + frame count (streaming; no full-file load)."""
    try:
        with wave.open(str(path), "rb") as wav:
            return {
                "channels": wav.getnchannels(),
                "sample_rate": wav.getframerate(),
                "sampwidth": wav.getsampwidth(),
                "duration_ms": round(wav.getnframes() / max(1, wav.getframerate()) * 1000),
            }
    except (wave.Error, OSError, EOFError) as exc:
        raise TTSAudioError(
            f"The synthesized audio '{path.name}' is not a readable WAV "
            f"file: {exc}"
        ) from exc


class PiperTTSProvider:
    """Piper CLI provider: one short-lived subprocess per segment.

    Executable resolution order: ``TTS_EXECUTABLE_PATH`` setting, then
    ``piper`` on PATH. Voice resolution: ``TTS_VOICE_<LANG>`` must point at
    a local ``.onnx`` model (Piper loads the sibling ``.onnx.json`` config
    automatically when present).
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._executable = self._resolve_executable()
        self._voice_paths: dict[str, Path | None] = {
            language: self._resolve_voice(field)
            for language, field in _LANGUAGE_FIELDS.items()
        }

    # -- resolution ------------------------------------------------------
    def _resolve_executable(self) -> str | None:
        configured = getattr(self._settings, "tts_executable_path", None)
        if configured:
            candidate = str(configured)
            if os.path.isfile(candidate):
                return candidate
            logger.warning(
                "Configured TTS_EXECUTABLE_PATH '%s' not found; trying PATH.",
                candidate,
            )
        for name in _PIPER_NAMES:
            found = shutil.which(name)
            if found:
                return found
        return None

    def _resolve_voice(self, field: str) -> Path | None:
        configured = getattr(self._settings, field, None)
        if not configured:
            return None
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = (Path(self._settings.base_dir) / path).resolve()
        return path  # may not exist; reported clearly by the caller

    # -- availability ----------------------------------------------------
    def _executable_error(self) -> str | None:
        if self._executable is None:
            return _engine_error_text()
        return None

    def _voice(self, language: str) -> tuple[Path | None, str | None]:
        """Return (model_path, error) for a language."""
        if language not in _LANGUAGE_FIELDS:
            return None, (
                f"Unsupported narration language '{language}'. Supported: "
                "en, hi, bn."
            )
        path = self._voice_paths.get(language)
        field = _LANGUAGE_FIELDS[language]
        if path is None:
            return None, _voice_error_text(language, field, missing=True)
        if not path.is_file():
            return None, _voice_error_text(language, field, missing=False)
        return path, None

    def available(self) -> bool:
        """Engine + at least one configured, existing voice model."""
        if self._executable_error() is not None:
            return False
        return any(
            self._voice(language)[1] is None for language in _LANGUAGE_FIELDS
        )

    # -- synthesis -------------------------------------------------------
    def synthesize(
        self,
        text: str,
        language: str,
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        if self._executable_error() is not None:
            raise TTSEngineUnavailableError(self._executable_error())
        model_path, voice_error = self._voice(language)
        if voice_error is not None:
            raise TTSVoiceMissingError(voice_error)

        out = output_path or (
            Path(self._settings.temp_dir) / "tts_segment.wav"
        )
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)

        args = [
            self._executable,
            "--model", str(model_path),
            "--output_file", str(out),
            "--length_scale", f"{self._settings.tts_length_scale:.2f}",
        ]
        config = Path(str(model_path) + ".json")
        if config.is_file():
            args += ["--config", str(config)]
        if self._settings.tts_speaker is not None:
            args += ["--speaker", str(self._settings.tts_speaker)]
        if os.name == "nt":  # pragma: no cover - Windows-only nicety
            args += ["--"]  # text arrives on stdin either way

        logger.info(
            "Piper synthesis (voice=%s, lang=%s, %d chars, timeout=%ss)",
            model_path.name, language, len(text), self._settings.tts_timeout_seconds,
        )
        flags: dict[str, object] = {}
        if os.name == "nt":  # pragma: no cover - Windows-only nicety
            flags["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **flags,  # type: ignore[arg-type]
            )
        except OSError as exc:  # pragma: no cover - raced deletion
            raise TTSEngineUnavailableError(
                f"Could not start Piper: {exc}"
            ) from exc

        try:
            _, stderr = proc.communicate(
                input=text.encode("utf-8"),
                timeout=self._settings.tts_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            try:
                out.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best-effort
                pass
            raise TTSTimeoutError(
                "Piper did not finish synthesizing this segment within "
                f"{self._settings.tts_timeout_seconds}s. The process was "
                "terminated. Try again, or raise TTS_TIMEOUT_SECONDS for "
                "very long segments."
            ) from None

        if proc.returncode != 0:
            try:
                out.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best-effort
                pass
            stderr_tail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()[-6:]
            logger.error(
                "Piper exited with code %s (voice=%s).",
                proc.returncode, model_path.name,
            )
            raise TTSGenerationError(
                "Piper failed to synthesize the segment "
                f"(exit code {proc.returncode}). "
                + (" ".join(stderr_tail) if stderr_tail else "")
            )

        if not out.is_file() or out.stat().st_size == 0:
            raise TTSGenerationError(
                "Piper produced no audio for the segment. Check the voice "
                f"model '{model_path.name}' - it may be corrupt or the wrong "
                "format (expected a Piper .onnx voice)."
            )

        facts = _wav_facts(out)
        if facts["duration_ms"] <= 0:
            raise TTSGenerationError(
                "Piper produced an empty audio segment (0 ms)."
            )
        facts.update({
            "path": str(out),
            "provider": "piper",
            "language": language,
            "voice_id": model_path.stem,
        })
        return facts

    # -- reporting -------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        exec_error = self._executable_error()
        languages: dict[str, Any] = {}
        voices: list[dict[str, Any]] = []
        for language in _LANGUAGE_FIELDS:
            model_path, voice_error = self._voice(language)
            available = exec_error is None and voice_error is None
            languages[language] = {
                "voice_id": model_path.stem if model_path else None,
                "available": available,
                "configured": model_path is not None,
                "model_available": model_path is not None and model_path.is_file(),
                "sample_rate": self._settings.tts_sample_rate,
                "note": None if available else voice_error,
            }
            if model_path:
                voices.append({
                    "id": f"{language}:{model_path.stem}",
                    "language": language,
                    "voice_id": model_path.stem,
                    "available": available,
                    "sample_rate": self._settings.tts_sample_rate,
                    "note": None if available else voice_error,
                })
        setup_hint: str | None = None
        if exec_error is not None:
            setup_hint = exec_error
        else:
            for language in _LANGUAGE_FIELDS:
                model_path, voice_error = self._voice(language)
                if voice_error is not None:
                    setup_hint = voice_error
                    break
        return {
            "provider": "piper",
            "available": self.available(),
            "executable_available": exec_error is None,
            "languages": languages,
            "voices": voices,
            "setup_hint": setup_hint,
        }


def build_tts_provider(settings: Any) -> LocalTTSProvider:
    """Create the configured provider (``none`` -> honest refusal)."""
    if getattr(settings, "tts_provider", "piper") == "none":
        return UnavailableProvider(
            "The local TTS stage is disabled (TTS_PROVIDER=none). Set it to "
            "'piper' and configure TTS_EXECUTABLE_PATH / TTS_VOICE_EN / "
            "TTS_VOICE_HI / TTS_VOICE_BN to generate narration."
        )
    return PiperTTSProvider(settings)


def tts_voice_identity(
    settings: Any,
    language: str | None = None,
) -> str | None:
    """Stable identity used in generation fingerprints (basename only)."""
    provider = PiperTTSProvider(settings)
    if language is not None:
        model_path, _ = provider._voice(language)  # noqa: SLF001
        return model_path.stem if model_path else None
    for code in _LANGUAGE_FIELDS:
        model_path, _ = provider._voice(code)  # noqa: SLF001
        if model_path:
            return model_path.stem
    return None


__all__ = [
    "LocalTTSProvider",
    "PiperTTSProvider",
    "UnavailableProvider",
    "TextToSpeechService",
    "build_tts_provider",
    "tts_voice_identity",
]
