"""Phase 6: PiperTTSProvider unit tests.

Runs against a tiny *fake* Piper CLI (a real Python script that writes a
valid WAV) so the provider's executable/voice resolution, timeout,
subprocess-failure and output-validation paths are exercised
deterministically without Piper. Subprocess tests need POSIX executables
(shebang + chmod), so they skip on Windows like the real-media tests do.
"""

from __future__ import annotations

import os
import stat
import textwrap
import wave
from pathlib import Path

import pytest

from app.ai.tts import (
    PiperTTSProvider,
    UnavailableProvider,
    build_tts_provider,
)
from app.config import Settings
from app.utils.errors import (
    TTSAudioError,
    TTSEngineUnavailableError,
    TTSGenerationError,
    TTSTimeoutError,
    TTSVoiceMissingError,
)

pytestmark = [
    pytest.mark.skipif(
        os.name == "nt",
        reason="Fake CLI executables (shebang + chmod) require POSIX.",
    ),
]

FAKE_PIPER = r"""#!/usr/bin/env python3
import os
import sys
import wave

mode = os.environ.get("FAKE_MODE", "")
if mode == "sleep":
    __import__("time").sleep(60)
    sys.exit(0)
if mode == "exit":
    sys.stderr.write("boom\n")
    sys.exit(3)
if mode == "garbage":
    out = sys.argv[sys.argv.index("--output_file") + 1]
    with open(out, "wb") as handle:
        handle.write(b"this is not a wav file at all")
    sys.exit(0)

out = None
for i, arg in enumerate(sys.argv):
    if arg == "--output_file":
        out = sys.argv[i + 1]
if out is None:
    sys.stderr.write("missing --output_file\n")
    sys.exit(2)

data = sys.stdin.buffer.read()
if mode == "empty":
    with open(out, "wb") as handle:
        handle.write(b"")
    sys.exit(0)

rate = int(os.environ.get("FAKE_RATE", "22050"))
frames = max(1, int(rate * (0.05 + 0.005 * len(data))))
with wave.open(out, "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(rate)
    payload = bytearray()
    step = 440 * 2 * 3.14159 / rate
    phase = 0.0
    for _ in range(frames):
        sample = int(9000 * __import__("math").sin(phase))
        payload += sample.to_bytes(2, "little", signed=True)
        phase += step
    wav.writeframes(bytes(payload))
sys.exit(0)
"""


@pytest.fixture
def piper_binary(tmp_path: Path) -> str:
    binary = tmp_path / "fake_piper"
    binary.write_text(textwrap.dedent(FAKE_PIPER), encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return str(binary)


def _voice(settings: Settings, tmp_path: Path, language: str) -> Path:
    voice = tmp_path / f"voice_{language}.onnx"
    voice.write_bytes(b"fake-model-content")
    setattr(settings, f"tts_voice_{language}", voice)
    return voice


def _settings_with(settings: Settings, tmp_path: Path, piper_binary: str | None) -> Settings:
    settings.tts_executable_path = piper_binary
    return settings


def test_missing_executable_reports_clearly(settings, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PATH", raising=False)
    settings.tts_executable_path = str(tmp_path / "does-not-exist-piper")
    provider = PiperTTSProvider(settings)
    assert provider.available() is False
    report = provider.describe()
    assert report["provider"] == "piper"
    assert report["available"] is False
    assert report["executable_available"] is False
    assert "piper" in (report["setup_hint"] or "").lower()
    assert "never downloads" in (report["setup_hint"] or "")
    with pytest.raises(TTSEngineUnavailableError):
        provider.synthesize("hello", "en", tmp_path / "x.wav")


def test_missing_voice_reports_clearly(settings, tmp_path, piper_binary) -> None:
    settings.tts_executable_path = piper_binary
    settings.tts_voice_en = None  # not configured at all
    provider = PiperTTSProvider(settings)
    # Engine present, voice missing -> not available for English.
    assert provider.available() is False
    report = provider.describe()
    en = report["languages"]["en"]
    assert en["configured"] is False
    assert en["available"] is False
    with pytest.raises(TTSVoiceMissingError) as exc:
        provider.synthesize("hello", "en", tmp_path / "x.wav")
    assert "TTS_VOICE_EN" in str(exc.value) or "No English voice" in str(exc.value)


def test_configured_voice_file_missing(settings, tmp_path, piper_binary) -> None:
    settings.tts_executable_path = piper_binary
    settings.tts_voice_hi = tmp_path / "missing-hi.onnx"
    provider = PiperTTSProvider(settings)
    assert provider.available() is False
    report = provider.describe()
    assert report["languages"]["hi"]["model_available"] is False
    with pytest.raises(TTSVoiceMissingError):
        provider.synthesize("नमस्ते", "hi", tmp_path / "x.wav")


def test_unsupported_language(settings, tmp_path, piper_binary) -> None:
    settings.tts_executable_path = piper_binary
    _voice(settings, tmp_path, "en")
    provider = PiperTTSProvider(settings)
    with pytest.raises(TTSVoiceMissingError) as exc:
        provider.synthesize("bonjour", "fr", tmp_path / "x.wav")
    assert "Unsupported" in str(exc.value)


def test_valid_synthesis_writes_measured_wav(settings, tmp_path, piper_binary) -> None:
    settings.tts_executable_path = piper_binary
    _voice(settings, tmp_path, "en")
    provider = PiperTTSProvider(settings)
    assert provider.available() is True
    out = tmp_path / "segment.wav"
    facts = provider.synthesize("Hello world, this is a test.", "en", output_path=out)
    assert out.is_file() and out.stat().st_size > 44
    assert facts["sample_rate"] == 22050
    assert facts["channels"] == 1
    assert facts["duration_ms"] > 0
    with wave.open(str(out), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 22050
        assert wav.getsampwidth() == 2


def test_synthesis_rate_follows_voice(settings, tmp_path, piper_binary, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_RATE", "16000")
    settings.tts_executable_path = piper_binary
    _voice(settings, tmp_path, "en")
    facts = PiperTTSProvider(settings).synthesize(
        "A different voice rate.", "en", tmp_path / "x.wav"
    )
    assert facts["sample_rate"] == 16000


def test_synthesis_timeout_kills_process(settings, tmp_path, piper_binary, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_MODE", "sleep")
    settings.tts_executable_path = piper_binary
    _voice(settings, tmp_path, "en")
    provider = PiperTTSProvider(settings)
    monkeypatch.setattr(provider._settings, "tts_timeout_seconds", 1)
    with pytest.raises(TTSTimeoutError):
        provider.synthesize("slow", "en", tmp_path / "x.wav")
    assert not (tmp_path / "x.wav").exists()  # partial output cleaned


def test_synthesis_subprocess_failure(settings, tmp_path, piper_binary, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_MODE", "exit")
    settings.tts_executable_path = piper_binary
    _voice(settings, tmp_path, "en")
    with pytest.raises(TTSGenerationError) as exc:
        PiperTTSProvider(settings).synthesize("boom", "en", tmp_path / "x.wav")
    assert "boom" in str(exc.value)


def test_malformed_output_audio_error(settings, tmp_path, piper_binary, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_MODE", "garbage")
    settings.tts_executable_path = piper_binary
    _voice(settings, tmp_path, "en")
    with pytest.raises(TTSAudioError):
        PiperTTSProvider(settings).synthesize("garbage", "en", tmp_path / "x.wav")


def test_empty_output_generation_error(settings, tmp_path, piper_binary, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_MODE", "empty")
    settings.tts_executable_path = piper_binary
    _voice(settings, tmp_path, "en")
    with pytest.raises(TTSGenerationError):
        PiperTTSProvider(settings).synthesize("empty", "en", tmp_path / "x.wav")


def test_provider_none_disables_honestly(settings) -> None:
    settings.tts_provider = "none"
    provider = build_tts_provider(settings)
    assert isinstance(provider, UnavailableProvider)
    assert provider.available() is False
    with pytest.raises(TTSEngineUnavailableError):
        provider.synthesize("x", "en", Path("x.wav"))
    assert provider.describe()["provider"] == "none"


def test_describe_never_leaks_full_paths(settings, tmp_path, piper_binary) -> None:
    settings.tts_executable_path = piper_binary
    voice = _voice(settings, tmp_path, "bn")
    provider = PiperTTSProvider(settings)
    report = provider.describe()
    assert report["languages"]["bn"]["voice_id"] == voice.stem
    assert report["languages"]["bn"]["available"] is True
    serialized = str(report)
    assert str(tmp_path) not in serialized  # no absolute path exposure
