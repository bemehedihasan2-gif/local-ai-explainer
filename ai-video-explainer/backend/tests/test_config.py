"""Check 9: configuration loads correctly (defaults + environment)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_are_relative_and_portable() -> None:
    settings = Settings()
    # No machine-specific absolute path should be baked in by default.
    assert settings.app_name == "Local AI Video Explainer"
    assert settings.backend_port == 8000
    assert settings.processing_concurrency == 1
    assert settings.max_upload_size_mb > 0
    assert settings.base_dir.is_absolute()


def test_directories_resolve_under_custom_base(tmp_path: Path) -> None:
    settings = Settings(base_dir=tmp_path / "root")
    assert settings.upload_dir == (tmp_path / "root" / "data" / "uploads").resolve()
    assert settings.output_dir == (tmp_path / "root" / "data" / "outputs").resolve()
    assert settings.model_dir == (tmp_path / "root" / "models").resolve()
    assert settings.database_path == (tmp_path / "root" / "data" / "explainer.db").resolve()


def test_environment_variables_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BACKEND_PORT", "8123")
    monkeypatch.setenv("BACKEND_HOST", "0.0.0.0")
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "512")
    monkeypatch.setenv("PROCESSING_CONCURRENCY", "1")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "custom-uploads"))

    settings = Settings()
    assert settings.backend_port == 8123
    assert settings.backend_host == "0.0.0.0"
    assert settings.max_upload_size_mb == 512
    assert settings.processing_concurrency == 1
    assert settings.log_level == "DEBUG"
    # Absolute override is honored verbatim.
    assert settings.upload_dir == (tmp_path / "custom-uploads").resolve()


def test_invalid_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="staging")
    with pytest.raises(ValidationError):
        Settings(processing_concurrency=0)


def test_storage_directories_named_entries(settings) -> None:
    names = set(settings.storage_directories.keys())
    assert {"uploads", "projects", "temp", "outputs", "cache", "logs"} <= names


def test_empty_tts_speaker_means_default_speaker() -> None:
    """Regression: a ``TTS_SPEAKER=`` (empty) line in .env must load as
    None, not fail with "Input should be a valid integer" (Windows
    FIRST_RUN.bat Step 7/9)."""
    assert Settings(tts_speaker="").tts_speaker is None
    assert Settings(tts_speaker=None).tts_speaker is None
    assert Settings(tts_speaker=3).tts_speaker == 3


def test_output_fps_accepts_numeric_strings() -> None:
    """Regression: ``OUTPUT_FPS=30`` arrives as the string "30" and must
    be accepted (it is documented as "a number, or 'source'")."""
    assert Settings(output_fps="30").output_fps == 30
    assert Settings(output_fps="source").output_fps == "source"
    assert Settings(output_fps="SOURCE").output_fps == "source"
    assert Settings(output_fps=24).output_fps == 24
    with pytest.raises(ValidationError):
        Settings(output_fps="banana")
    with pytest.raises(ValidationError):
        Settings(output_fps="61")


def test_empty_optional_paths_are_none() -> None:
    """Regression: empty ``KEY=`` lines in .env for optional executables /
    models / voices must resolve to None (auto-discover / not configured),
    not ``Path('.')`` which would be treated as a configured path."""
    s = Settings(
        ffmpeg_path="",
        ffprobe_path="",
        tesseract_path="",
        llama_cpp_path="",
        llama_model_path="",
        tts_executable_path="",
        tts_voice_en="",
        tts_voice_hi="",
        tts_voice_bn="",
        subtitle_font_path="",
        subtitle_font_name="",
    )
    assert s.ffmpeg_path is None
    assert s.ffprobe_path is None
    assert s.tesseract_path is None
    assert s.llama_cpp_path is None
    assert s.llama_model_path is None
    assert s.tts_executable_path is None
    assert s.tts_voice_en is None
    assert s.tts_voice_hi is None
    assert s.tts_voice_bn is None
    assert s.subtitle_font_path is None
    assert s.subtitle_font_name is None
