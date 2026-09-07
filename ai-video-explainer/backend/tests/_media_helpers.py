"""Shared helpers for the Phase 2 upload tests.

Strategy: the sandbox (and some user machines) may not have FFmpeg, so the
HTTP-level tests run against *fake* ffmpeg/ffprobe executables that mimic the
real binaries' ``-version`` behavior and return controlled FFprobe JSON. This
exercises the full upload engine (storage, hashing, statuses, cleanup,
duplicates) deterministically.

Tests that genuinely need real media decoding live in
``test_upload_real_media.py`` and skip cleanly when FFmpeg is absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import textwrap
from pathlib import Path


def _write_executable(path: Path, source: str) -> str:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def probe_payload(
    *,
    width: int = 1920,
    height: int = 1080,
    r_frame_rate: str = "30000/1001",
    duration: float = 12.5,
    format_name: str = "mov,mp4,m4a,3gp,3g2,mj2",
    with_audio: bool = True,
    audio_codec: str = "aac",
    bit_rate: str = "1500000",
    video_codec: str = "h264",
    no_video_stream: bool = False,
) -> str:
    """Build an FFprobe-style JSON document for the fake prober."""
    streams: list[dict[str, object]] = []
    if not no_video_stream:
        streams.append(
            {
                "codec_type": "video",
                "codec_name": video_codec,
                "width": width,
                "height": height,
                "r_frame_rate": r_frame_rate,
                "duration": str(duration),
            }
        )
    if with_audio:
        streams.append(
            {"codec_type": "audio", "codec_name": audio_codec, "duration": str(duration)}
        )
    return json.dumps(
        {
            "streams": streams,
            "format": {
                "format_name": format_name,
                "duration": str(duration),
                "size": "99999",
                "bit_rate": bit_rate,
            },
        }
    )


def _fake_ffmpeg_source(preprocess_mode: str = "ok") -> str:
    """Source for the fake ffmpeg executable.

    ``preprocess_mode`` controls behavior when ``-progress`` is present
    (Phase 3 preprocessing invocations):
      - "ok": emits duration-shaped ``out_time_us`` progress lines, writes
        a non-empty file at the final argv entry (the output path) and
        exits 0.
      - "slow": like "ok", but sleeps between progress lines so tests can
        observe intermediate job/project states.
      - "fail": prints an ffmpeg-style error to stderr and exits 1.
    """
    if preprocess_mode == "fail":
        behavior = (
            "    print('fake ffmpeg error: Invalid data found when processing input', "
            "file=sys.stderr)\n"
            "    sys.exit(1)\n"
        )
    elif preprocess_mode == "slow":
        behavior = (
            "    import time\n"
            "    time.sleep(0.2)\n"
            "    print('out_time_us=0')\n"
            "    time.sleep(0.2)\n"
            "    print('out_time_us=6000000')\n"
            "    time.sleep(0.2)\n"
            "    print('out_time_us=12500000')\n"
            "    print('progress=end')\n"
            "    with open(sys.argv[-1], 'wb') as out:\n"
            "        out.write(b'fake-asset')\n"
            "    sys.exit(0)\n"
        )
    else:
        behavior = (
            "    for i in range(1, 26):\n"
            "        print(f'out_time_us={i * 500000}')\n"
            "    print('progress=end')\n"
            "    with open(sys.argv[-1], 'wb') as out:\n"
            "        out.write(b'fake-asset')\n"
            "    sys.exit(0)\n"
        )
    return (
        f"#!{sys.executable}\n"
        "import sys\n"
        "if '-version' in sys.argv:\n"
        "    print('ffmpeg version 7.1.1-fake Copyright (c) 2000-2024 the FFmpeg developers')\n"
        "    sys.exit(0)\n"
        "if '-progress' in sys.argv:\n"
        + behavior
        + "print('fake ffmpeg: no transcoding in tests')\n"
    )


def install_fake_media_tools(
    tmp_path: Path,
    *,
    probe_body: str | None = None,
    probe_mode: str = "ok",
    preprocess_mode: str = "ok",
) -> tuple[str, str]:
    """Create fake ``ffmpeg`` + ``ffprobe`` executables under ``tmp_path``.

    Returns (ffmpeg_path, ffprobe_path). ``probe_mode``:
      - "ok": prints ``probe_body`` (or a default video+audio document)
      - "exit_error": exits non-zero with an ffprobe-style error (corrupt file)
      - "empty_streams": valid JSON but no streams at all

    ``preprocess_mode`` (see :func:`_fake_ffmpeg_source`): "ok" | "slow" |
    "fail" - controls the fake ffmpeg's ``-progress`` behavior used by the
    Phase 3 preprocessing service.
    """
    ffmpeg_src = _fake_ffmpeg_source(preprocess_mode)

    # Every mode answers ``-version`` first so binary detection succeeds;
    # the behavior after that differs per probe_mode.
    version_lines = (
        "import sys\n"
        "if '-version' in sys.argv:\n"
        "    print('ffprobe version 7.1.1-fake Copyright (c) 2000-2024 the FFmpeg developers')\n"
        "    sys.exit(0)\n"
    )
    if probe_mode == "exit_error":
        behavior = (
            "print('ffprobe error: Invalid data found when processing input', file=sys.stderr)\n"
            "sys.exit(1)\n"
        )
    elif probe_mode == "empty_streams":
        behavior = "print(json.dumps({'streams': [], 'format': {'format_name': 'mp4'}}))\n"
    else:
        body = probe_body or probe_payload()  # already a JSON document string
        # json.dumps(body) renders a quoted Python literal of the document.
        behavior = f"print({json.dumps(body)})\n"

    probe_src = f"#!{sys.executable}\nimport json\n" + version_lines + behavior

    ffmpeg = _write_executable(tmp_path / "ffmpeg-fake", ffmpeg_src)
    ffprobe = _write_executable(tmp_path / "ffprobe-fake", probe_src)
    return ffmpeg, ffprobe


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def real_ffmpeg_available() -> bool:
    """True when a real FFmpeg + FFprobe pair exists on PATH (or configured)."""
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not (ffmpeg and ffprobe):
        return False
    try:
        for binary in (ffmpeg, ffprobe):
            result = subprocess.run(
                [binary, "-version"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def generate_real_video(
    ffmpeg_bin: str,
    out_path: Path,
    *,
    duration_s: float = 1.0,
    with_audio: bool = True,
) -> Path:
    """Generate a tiny synthetic video with real FFmpeg (testsrc/sine)."""
    cmd = [
        ffmpeg_bin, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={duration_s}",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"]
    else:
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an"]
    cmd += [str(out_path)]
    import subprocess

    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return out_path


def where_ffmpeg() -> str | None:
    import shutil

    return shutil.which("ffmpeg")


def where_ffprobe() -> str | None:
    import shutil

    return shutil.which("ffprobe")
