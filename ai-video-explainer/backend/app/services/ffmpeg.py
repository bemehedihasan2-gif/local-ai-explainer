"""FFmpeg / ffprobe discovery.

Phase 1 never downloads FFmpeg and never shells out with user input. This
service only *detects* the binaries (explicit path from config, otherwise
PATH lookup) and reports versions, so the UI can show clear setup guidance
when FFmpeg is missing. ``subprocess`` calls are always non-shell.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.errors import FFmpegUnavailableError
from app.utils.logging import get_logger

logger = get_logger("app.services.ffmpeg")

VERSION_RE = re.compile(r"version\s+([0-9][^\s]*)", re.IGNORECASE)

SETUP_HINT = (
    "FFmpeg is required for video processing (available from Phase 2). "
    "Install it from https://ffmpeg.org/download.html (e.g. 'winget install "
    "ffmpeg' or the gyan.dev build) and ensure ffmpeg/ffprobe are on PATH, "
    "or set FFMPEG_PATH/FFPROBE_PATH in .env."
)


def _version_of_binary(path: str) -> tuple[bool, str | None]:
    """Run ``<bin> -version``; return (ran_ok, version_or_None).

    Some FFmpeg builds print the version banner on stdout, others on
    stderr, so both streams are considered. ``ran_ok`` is True only when
    the process actually started and exited 0 - a configured path that
    exists but cannot run (wrong architecture, corrupt file, model file)
    is reported as *not available*, never as available-with-unknown-
    version.
    """
    flags: dict[str, object] = {}
    if os.name == "nt":  # pragma: no cover - Windows-only nicety
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **flags,  # type: ignore[arg-type]
        )
    except OSError as exc:
        # Windows raises OSError/WinError 193 for files that are not valid
        # Win32 executables (e.g. a Linux binary or a model file pointed at
        # by FFMPEG_PATH). That is an honest "not available".
        logger.warning("Could not execute '%s -version': %s", path, exc)
        return False, None
    except subprocess.SubprocessError as exc:
        logger.warning("Could not execute '%s -version': %s", path, exc)
        return False, None
    if proc.returncode != 0:
        logger.warning("'%s -version' exited with code %s", path, proc.returncode)
        return False, None
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    first_line = next((ln.strip() for ln in combined.splitlines() if ln.strip()), "")
    match = VERSION_RE.search(first_line)
    return True, (match.group(1) if match else first_line[:60] or "unknown")


@dataclass
class FfmpegStatus:
    """Snapshot of FFmpeg availability, safe to serialize to JSON."""

    ffmpeg_available: bool = False
    ffmpeg_version: str | None = None
    ffmpeg_path: str | None = None
    ffprobe_available: bool = False
    ffprobe_version: str | None = None
    ffprobe_path: str | None = None
    setup_hint: str | None = None

    @property
    def available(self) -> bool:
        return self.ffmpeg_available and self.ffprobe_available

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "ffmpeg": {
                "available": self.ffmpeg_available,
                "version": self.ffmpeg_version,
                "path": self.ffmpeg_path,
            },
            "ffprobe": {
                "available": self.ffprobe_available,
                "version": self.ffprobe_version,
                "path": self.ffprobe_path,
            },
            "setup_hint": self.setup_hint,
        }


class FfmpegService:
    """Locates FFmpeg/ffprobe and reports their availability & versions."""

    def __init__(self, settings) -> None:
        self._settings = settings

    # -- lookup ----------------------------------------------------------
    def _resolve(self, configured: str | Path | None, name: str) -> str | None:
        """Windows-safe resolution: configured path first, then PATH.

        A configured path counts only when it is a regular file (a
        directory is never treated as an executable). ``shutil.which``
        handles ``ffmpeg.exe``/``ffprobe.exe`` on Windows via PATHEXT.
        """
        if configured:
            candidate = str(configured)
            if os.path.isfile(candidate) and not os.path.isdir(candidate):
                return candidate
            logger.warning(
                "Configured %s path '%s' is not a file; falling back to PATH.",
                name, candidate,
            )
        return shutil.which(name)

    # -- detection -------------------------------------------------------
    def detect(self) -> FfmpegStatus:
        status = FfmpegStatus()
        ffmpeg_path = self._resolve(self._settings.ffmpeg_path, "ffmpeg")
        ffprobe_path = self._resolve(self._settings.ffprobe_path, "ffprobe")

        if ffmpeg_path:
            ok, version = _version_of_binary(ffmpeg_path)
            status.ffmpeg_available = ok
            status.ffmpeg_version = version
            status.ffmpeg_path = str(ffmpeg_path)
        if ffprobe_path:
            ok, version = _version_of_binary(ffprobe_path)
            status.ffprobe_available = ok
            status.ffprobe_version = version
            status.ffprobe_path = str(ffprobe_path)

        if not status.available:
            status.setup_hint = SETUP_HINT
            logger.warning(
                "FFmpeg detection: ffmpeg=%s ffprobe=%s",
                status.ffmpeg_available, status.ffprobe_available,
            )
        else:
            logger.info(
                "FFmpeg ready: %s / %s",
                status.ffmpeg_version, status.ffprobe_version,
            )
        return status

    def require(self) -> FfmpegStatus:
        """Like :meth:`detect` but raises when FFmpeg is unavailable."""
        status = self.detect()
        if not status.available:
            raise FFmpegUnavailableError()
        return status


__all__ = ["FfmpegService", "FfmpegStatus"]
