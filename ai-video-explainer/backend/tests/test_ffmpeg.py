"""Check 8: FFmpeg detection is graceful in both states.

- FFmpeg missing  -> available=False + actionable setup hint (no crash).
- FFmpeg present  -> versions are parsed from a real binary invocation.
"""

from __future__ import annotations

import os
import shutil
import stat

import pytest

from app.services.ffmpeg import FfmpegService
from app.utils.errors import FFmpegUnavailableError


def _fake_binary(tmp_path, name: str, version_line: str) -> str:
    """Create an executable script that mimics ``<bin> -version``."""
    path = tmp_path / name
    path.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' " f"'{version_line}'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_detection_when_ffmpeg_missing(settings, tmp_path, monkeypatch) -> None:
    # Configure explicit paths that cannot exist, and force the PATH
    # lookup to fail too - so the "missing" state is deterministic on ANY
    # machine (including ones with real FFmpeg installed).
    monkeypatch.setattr(shutil, "which", lambda name: None)
    settings.ffmpeg_path = tmp_path / "missing" / "ffmpeg.exe"
    settings.ffprobe_path = tmp_path / "missing" / "ffprobe.exe"
    status = FfmpegService(settings).detect()

    assert status.ffmpeg_available is False
    assert status.ffprobe_available is False
    assert status.available is False
    assert status.setup_hint  # clear, actionable guidance
    assert "ffmpeg" in status.setup_hint.lower()


def test_require_raises_when_missing(settings, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    settings.ffmpeg_path = tmp_path / "nope" / "ffmpeg"
    settings.ffprobe_path = tmp_path / "nope" / "ffprobe"
    try:
        FfmpegService(settings).require()
    except FFmpegUnavailableError as exc:
        assert exc.status_code == 503
        assert "Install FFmpeg" in exc.message
    else:
        raise AssertionError("require() should raise when FFmpeg is missing")


@pytest.mark.skipif(
    os.name == "nt",
    reason="Fake CLI executables (shebang + chmod) require POSIX.",
)
def test_detection_when_ffmpeg_present(settings, tmp_path) -> None:
    settings.ffmpeg_path = _fake_binary(tmp_path, "ffmpeg", "ffmpeg version 7.1.1-fake Copyright (c) 2000-2024 the FFmpeg developers")
    settings.ffprobe_path = _fake_binary(tmp_path, "ffprobe", "ffprobe version 7.1.1-fake Copyright (c) 2000-2024 the FFmpeg developers")

    status = FfmpegService(settings).detect()
    assert status.available is True
    assert status.ffmpeg_version == "7.1.1-fake"
    assert status.ffprobe_version == "7.1.1-fake"
    assert status.setup_hint is None

    payload = status.to_dict()
    assert payload["ffmpeg"]["available"] is True
    assert payload["ffprobe"]["version"].startswith("7.1.1")
