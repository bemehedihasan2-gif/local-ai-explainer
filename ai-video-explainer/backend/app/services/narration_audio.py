"""Narration audio helpers (Phase 6).

Everything here is **streamed** - a narration is assembled and measured in
bounded chunks, never loaded into Python memory whole (a 4-minute mono
22 kHz narration is ~10 MB, but the principle keeps the pipeline safe for
much longer outputs on the 8 GB target).

Assembly/normalization use Python's stdlib ``wave`` module: an equivalent
local audio tool to FFmpeg that needs no extra binary, so narration never
depends on FFmpeg (Phase 7 still uses FFmpeg to mix + render the final
video). Sample formats are validated on every step and never guessed:
actual TTS segment durations are measured from the generated WAV files.
"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path
from typing import Any, Iterable

from app.utils.errors import NarrationError, TTSAudioError
from app.utils.logging import get_logger

logger = get_logger("app.services.narration_audio")

#: Frames read per streaming chunk (0.25 s at 22 050 Hz).
_CHUNK_FRAMES = 8192


def read_wav_info(path: Path | str) -> dict[str, Any]:
    """Read a WAV header + frame count without loading audio data."""
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as wav:
            info = {
                "channels": wav.getnchannels(),
                "sample_rate": wav.getframerate(),
                "sampwidth": wav.getsampwidth(),
                "frames": wav.getnframes(),
            }
    except (wave.Error, OSError, EOFError) as exc:
        raise TTSAudioError(
            f"'{path.name}' is not a readable WAV file: {exc}"
        ) from exc
    info["duration_ms"] = round(
        info["frames"] / max(1, info["sample_rate"]) * 1000
    )
    info["path"] = str(path)
    return info


def validate_segment_wav(
    path: Path | str,
    *,
    expected_rate: int | None = None,
    expected_channels: int = 1,
) -> dict[str, Any]:
    """Validate one synthesized segment; raise on any mismatch."""
    path = Path(path)
    info = read_wav_info(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise TTSAudioError(f"Segment '{path.name}' is missing or empty.")
    if info["sampwidth"] != 2:
        raise TTSAudioError(
            f"Segment '{path.name}' is not 16-bit PCM (sampwidth="
            f"{info['sampwidth']}). Piper emits s16le; a different voice "
            "engine must be configured to match."
        )
    if info["channels"] != expected_channels:
        raise TTSAudioError(
            f"Segment '{path.name}' has {info['channels']} channel(s); "
            f"expected {expected_channels}. Voices must emit matching "
            "mono/stereo output."
        )
    if expected_rate is not None and info["sample_rate"] != expected_rate:
        raise TTSAudioError(
            f"Segment '{path.name}' sample rate {info['sample_rate']} Hz "
            f"does not match the voice's {expected_rate} Hz. All segments "
            "of one narration must share one voice/model."
        )
    if info["duration_ms"] <= 0:
        raise TTSAudioError(f"Segment '{path.name}' has zero duration.")
    return info


def scan_levels(path: Path | str) -> dict[str, Any]:
    """One streaming pass: peak/RMS levels + clip ratio of a s16 WAV."""
    path = Path(path)
    info = read_wav_info(path)
    if info["sampwidth"] != 2:
        raise TTSAudioError(
            f"'{path.name}' is not 16-bit PCM; level scanning requires s16."
        )
    samples = 0
    sum_squares = 0.0
    peak = 0
    clipped = 0
    with wave.open(str(path), "rb") as wav:
        while True:
            frames = wav.readframes(_CHUNK_FRAMES)
            if not frames:
                break
            values = array.array("h")
            values.frombytes(frames)
            count = len(values)
            if count == 0:
                continue
            samples += count
            for value in values:
                magnitude = abs(int(value))
                if magnitude > peak:
                    peak = magnitude
                if magnitude >= 32760:
                    clipped += 1
                sum_squares += magnitude * magnitude
    mean_rms = math.sqrt(sum_squares / samples) if samples else 0.0
    return {
        "path": str(path),
        "samples": samples,
        "peak": peak,
        "peak_db": _db(peak),
        "mean_db": _db(mean_rms),
        "clipped_samples": clipped,
        "clip_ratio": round(clipped / max(1, samples), 6),
    }


def _db(linear: float) -> float:
    if linear <= 0:
        return -120.0
    return round(20.0 * math.log10(linear / 32768.0), 2)


def assemble_narration(
    entries: Iterable[dict[str, Any]],
    out_path: Path | str,
    *,
    sample_rate: int,
    channels: int = 1,
) -> dict[str, Any]:
    """Concatenate WAV segments, inserting per-entry ``gap_ms`` silence.

    ``entries`` is an ordered iterable of ``{"path": ..., "gap_ms": ...}``
    (gap silence plays *before* that entry). All segments must be 16-bit
    mono PCM at ``sample_rate`` - consistency is validated per file. The
    output is written to ``out_path`` in one streaming pass.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if channels not in (1, 2):
        raise NarrationError(f"Unsupported channel count {channels}.")

    entry_list = list(entries)
    total_ms = 0
    expected = (channels, 2, sample_rate)  # channels, sampwidth, rate
    try:
        with wave.open(str(out_path), "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(2)
            out.setframerate(sample_rate)
            silence_frame = b"\x00\x00" * channels
            for entry in entry_list:
                gap_ms = int(entry.get("gap_ms") or 0)
                if gap_ms > 0:
                    gap_frames = round(sample_rate * gap_ms / 1000)
                    out.writeframes(silence_frame * gap_frames)
                    total_ms += gap_ms
                segment_path = Path(entry["path"])
                info = validate_segment_wav(
                    segment_path,
                    expected_rate=sample_rate,
                    expected_channels=channels,
                )
                actual = (info["channels"], info["sampwidth"], info["sample_rate"])
                if actual != expected:
                    raise NarrationError(
                        f"Segment '{segment_path.name}' format {actual} does "
                        f"not match the run's {expected}."
                    )
                with wave.open(str(segment_path), "rb") as segment:
                    while True:
                        frames = segment.readframes(_CHUNK_FRAMES)
                        if not frames:
                            break
                        out.writeframes(frames)
                    total_ms += info["duration_ms"]
    except TTSAudioError:
        raise
    except (OSError, wave.Error) as exc:
        raise NarrationError(f"Could not assemble narration audio: {exc}") from exc

    final = read_wav_info(out_path)
    if final["duration_ms"] <= 0:
        raise NarrationError(
            "Narration assembly produced an empty audio file."
        )
    logger.info(
        "Assembled narration.wav: %d ms, %d Hz, %d ch (%d segments).",
        final["duration_ms"], final["sample_rate"], final["channels"],
        len(entry_list),
    )
    return final


def normalize_loudness(
    path: Path | str,
    *,
    target_mean_db: float = -20.0,
    max_gain_db: float = 12.0,
    peak_ceiling_db: float = -1.5,
) -> dict[str, Any]:
    """Apply safe loudness normalization to a s16 WAV (streamed).

    Only *raises* quiet narration: gain is clamped so the result never
    clips and never exceeds ``max_gain_db`` (no aggressive amplification,
    natural dynamics survive). Re-writes the file in place when a gain of
    >= 0.3 dB is warranted; otherwise reports measured levels unchanged.
    """
    path = Path(path)
    before = scan_levels(path)
    if before["mean_db"] < -80.0:
        # Effectively silent: amplifying zeros is pointless and dangerous
        # (it would turn quantization noise into audible hiss).
        return {
            "applied_gain_db": 0.0,
            "before": {k: before[k] for k in ("peak_db", "mean_db", "clip_ratio")},
            "after": {k: before[k] for k in ("peak_db", "mean_db", "clip_ratio")},
        }
    gain_db = target_mean_db - before["mean_db"]
    # Never push the peak above the ceiling.
    gain_to_ceiling = peak_ceiling_db - before["peak_db"]
    applied_gain_db = min(gain_db, gain_to_ceiling, max_gain_db)
    if before["peak_db"] > peak_ceiling_db:
        # Already louder than the ceiling: reduce gently to the ceiling.
        applied_gain_db = max(-max_gain_db, min(0.0, gain_to_ceiling))

    result: dict[str, Any] = {
        "applied_gain_db": round(applied_gain_db, 2),
        "before": {k: before[k] for k in ("peak_db", "mean_db", "clip_ratio")},
    }
    if abs(applied_gain_db) < 0.3:
        result["after"] = result["before"]
        return result

    factor = 10 ** (applied_gain_db / 20.0)
    tmp = path.with_suffix(".wav.norm.tmp")
    try:
        with wave.open(str(path), "rb") as src, wave.open(str(tmp), "wb") as dst:
            params = src.getparams()
            dst.setparams(params)
            while True:
                frames = src.readframes(_CHUNK_FRAMES)
                if not frames:
                    break
                values = array.array("h")
                values.frombytes(frames)
                scaled = array.array("h", values)
                for i, value in enumerate(values):
                    scaled_value = int(value * factor)
                    if scaled_value > 32767:
                        scaled_value = 32767
                    elif scaled_value < -32768:
                        scaled_value = -32768
                    scaled[i] = scaled_value
                dst.writeframes(scaled.tobytes())
        tmp.replace(path)
    except (OSError, wave.Error) as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best-effort
            pass
        raise NarrationError(f"Loudness normalization failed: {exc}") from exc

    after = scan_levels(path)
    result["after"] = {k: after[k] for k in ("peak_db", "mean_db", "clip_ratio")}
    logger.info(
        "Normalized narration gain=%.2f dB (mean %.1f -> %.1f dBFS).",
        applied_gain_db, before["mean_db"], after["mean_db"],
    )
    return result


__all__ = [
    "read_wav_info",
    "validate_segment_wav",
    "scan_levels",
    "assemble_narration",
    "normalize_loudness",
]
