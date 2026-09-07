"""Phase 6: narration audio helper tests (stdlib WAV streaming).

Synthesizes small sine WAVs with the ``wave`` module, then exercises
validation, gap assembly and safe normalization - no FFmpeg needed.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from app.services.narration_audio import (
    assemble_narration,
    normalize_loudness,
    read_wav_info,
    scan_levels,
    validate_segment_wav,
)
from app.utils.errors import NarrationError, TTSAudioError

RATE = 22050


def _write_wav(
    path: Path, frames: int, *, rate: int = RATE,
    amplitude: int = 9000, square: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        step = 2 * math.pi * 440 / rate
        payload = bytearray()
        for i in range(frames):
            raw = amplitude if square else int(amplitude * math.sin(step * i))
            sample = max(-32767, min(32767, raw))
            payload += struct.pack("<h", sample)
        wav.writeframes(bytes(payload))


def test_read_wav_info_measures_duration(tmp_path: Path) -> None:
    path = tmp_path / "a.wav"
    _write_wav(path, RATE // 2)  # 0.5 s
    info = read_wav_info(path)
    assert info["sample_rate"] == RATE
    assert info["channels"] == 1
    assert info["sampwidth"] == 2
    assert 495 <= info["duration_ms"] <= 505


def test_read_wav_info_rejects_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.wav"
    path.write_bytes(b"RIFF not really a wave file")
    with pytest.raises(TTSAudioError):
        read_wav_info(path)


def test_validate_segment_wav_mismatches(tmp_path: Path) -> None:
    good = tmp_path / "good.wav"
    _write_wav(good, RATE)
    assert validate_segment_wav(good, expected_rate=RATE, expected_channels=1)

    stereo = tmp_path / "stereo.wav"
    with wave.open(str(stereo), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"\x00\x00\x00\x00" * RATE)
    with pytest.raises(TTSAudioError, match="channel"):
        validate_segment_wav(stereo, expected_rate=RATE, expected_channels=1)

    with pytest.raises(TTSAudioError, match="rate"):
        validate_segment_wav(good, expected_rate=16000, expected_channels=1)


def test_assemble_narration_inserts_gaps_in_order(tmp_path: Path) -> None:
    seg_a = tmp_path / "segments" / "a.wav"
    seg_b = tmp_path / "segments" / "b.wav"
    _write_wav(seg_a, RATE // 4)   # 250 ms
    _write_wav(seg_b, RATE // 4)   # 250 ms
    out = tmp_path / "narration.wav"

    assembled = assemble_narration(
        [
            {"path": seg_a, "gap_ms": 200},
            {"path": seg_b, "gap_ms": 300},
        ],
        out,
        sample_rate=RATE,
        channels=1,
    )
    # 200 + 250 + 300 + 250 = 1000 ms
    assert 980 <= assembled["duration_ms"] <= 1020
    info = read_wav_info(out)
    assert info["sample_rate"] == RATE
    assert info["channels"] == 1
    # Leading silence is real silence (zeros), not tone.
    with wave.open(str(out), "rb") as wav:
        head = wav.readframes(RATE // 10)  # first 100 ms = silence
        assert set(head) <= {0, 0}


def test_assemble_rejects_mixed_sample_rates(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _write_wav(a, RATE // 10)
    _write_wav(b, RATE // 10, rate=16000)
    with pytest.raises(TTSAudioError, match="does not match"):
        assemble_narration(
            [{"path": a, "gap_ms": 0}, {"path": b, "gap_ms": 0}],
            tmp_path / "out.wav",
            sample_rate=RATE,
            channels=1,
        )


def test_scan_levels_reports_peak_and_clipping(tmp_path: Path) -> None:
    quiet = tmp_path / "quiet.wav"
    _write_wav(quiet, RATE // 4, amplitude=4000)
    levels = scan_levels(quiet)
    assert levels["mean_db"] < -10
    assert levels["clip_ratio"] == 0

    clipped = tmp_path / "clipped.wav"
    _write_wav(clipped, RATE // 4, amplitude=32767, square=True)
    clipped_levels = scan_levels(clipped)
    assert clipped_levels["clip_ratio"] > 0


def test_normalize_quiet_audio_without_clipping(tmp_path: Path) -> None:
    quiet = tmp_path / "quiet.wav"
    _write_wav(quiet, RATE // 2, amplitude=2000)  # ~ -24 dBFS peak
    before = scan_levels(quiet)

    result = normalize_loudness(
        quiet,
        target_mean_db=-20.0,
        max_gain_db=12.0,
        peak_ceiling_db=-1.5,
    )
    assert result["applied_gain_db"] > 0
    after = scan_levels(quiet)
    # Loudness went up but the peak stayed under the ceiling.
    assert after["mean_db"] > before["mean_db"]
    assert after["peak_db"] <= -1.4
    assert after["clip_ratio"] == 0


def test_normalize_loud_audio_reduces_to_ceiling(tmp_path: Path) -> None:
    loud = tmp_path / "loud.wav"
    _write_wav(loud, RATE // 2, amplitude=30000)  # ~ -0.8 dBFS peak
    result = normalize_loudness(
        loud,
        target_mean_db=-20.0,
        max_gain_db=12.0,
        peak_ceiling_db=-1.5,
    )
    assert result["applied_gain_db"] < 0
    assert scan_levels(loud)["peak_db"] <= -1.4


def test_normalize_silence_warns_not_crashes(tmp_path: Path) -> None:
    silent = tmp_path / "silent.wav"
    with wave.open(str(silent), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"\x00\x00" * RATE)
    before = scan_levels(silent)
    assert before["mean_db"] < -100
    result = normalize_loudness(silent, max_gain_db=12.0, target_mean_db=-20.0, peak_ceiling_db=-1.5)
    assert result["applied_gain_db"] <= 0  # never amplifies pure silence
