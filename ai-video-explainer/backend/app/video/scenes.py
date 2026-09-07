"""Scene detection & representative-frame extraction (Phase 4).

Deterministic, local, and dependency-free beyond FFmpeg itself:

- Boundaries are found with the built-in ``select='gt(scene,TH)'`` filter
  (the same ContentDetector-style algorithm PySceneDetect wraps, running
  inside FFmpeg - zero extra Python packages, zero RAM for the video).
- Representative frames are pulled from the **analysis copy** in a single
  decode pass via ``select='eq(n,N)+...'`` with ``-vsync vfr`` - frame
  indices are exact because the analysis copy has a constant fps (Phase 3).

Both passes stream through FFmpeg; Python memory stays flat.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from app.utils.errors import FFmpegUnavailableError, SceneDetectionError
from app.utils.logging import get_logger

logger = get_logger("app.video.scenes")

#: stderr line emitted by showinfo for every frame: "... pts_time:12.400 ..."
_PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def _progress_lines(proc: subprocess.Popen, duration: float, window: tuple[float, float],
                    callback: Callable[[float], None] | None) -> None:
    """Consume ``-progress pipe:1`` lines; report window-mapped progress."""
    start, end = window
    span = end - start
    last = start
    assert proc.stdout is not None
    for raw in proc.stdout:
        if raw.startswith("out_time_us="):
            try:
                elapsed = int(raw[len("out_time_us="):].strip()) / 1_000_000.0
            except ValueError:
                continue
            if duration > 0 and callback is not None:
                pct = start + min(1.0, elapsed / duration) * span
                if pct - last >= 0.5:
                    last = pct
                    callback(round(pct, 1))
    if callback is not None:
        callback(end)


def detect_scenes(
    ffmpeg_path: str,
    analysis_path: str | Path,
    *,
    threshold: float,
    min_duration: float,
    max_scenes: int,
    duration: float,
    timeout_seconds: int,
    progress_callback: Callable[[float], None] | None = None,
) -> list[dict[str, Any]]:
    """Return scene records ``{scene_id, start, end, duration,
    representative_timestamp}`` (1-based ids, sorted).

    Raises :class:`SceneDetectionError` when the pass fails or the output
    is unusable. Scenes are merged so every scene satisfies ``min_duration``
    and the total never exceeds ``max_scenes``.
    """
    source = Path(analysis_path)
    if not source.is_file() or source.stat().st_size == 0:
        raise SceneDetectionError(
            f"Analysis video '{source.name}' is missing or empty; "
            "re-run preprocessing first."
        )

    flags: dict[str, object] = {}
    if os.name == "nt":  # pragma: no cover - Windows-only nicety
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW
    cmd = [
        ffmpeg_path, "-v", "info", "-progress", "pipe:1", "-nostats",
        "-i", str(source),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    # showinfo emits one stderr line per frame (thousands on long videos), so
    # stderr goes to a temp FILE - a pipe could fill and deadlock while we
    # drain stdout progress. The file is streamed by ffmpeg, RAM stays flat.
    stderr_fd, stderr_path = tempfile.mkstemp(prefix="scenes-", suffix=".log")
    os.close(stderr_fd)
    try:
        with open(stderr_path, "wb") as stderr_file:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=stderr_file,
                text=True, encoding="utf-8", errors="replace", **flags,  # type: ignore[arg-type]
            )
            try:
                deadline = time.monotonic() + timeout_seconds
                _progress_lines(proc, duration, (0.0, 1.0), progress_callback)
                remaining = deadline - time.monotonic()
                proc.wait(timeout=max(0.1, remaining))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise SceneDetectionError(
                    f"Scene detection timed out after {timeout_seconds}s."
                ) from None
        stderr = Path(stderr_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SceneDetectionError(f"Scene detection failed: {exc}") from exc
    finally:
        try:
            Path(stderr_path).unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best-effort
            pass

    if proc.returncode != 0:
        detail = (stderr or "").strip().splitlines()[-1][:300] if stderr.strip() else "unknown error"
        raise SceneDetectionError(f"FFmpeg scene detection failed: {detail}")

    changes: list[float] = []
    for line in (stderr or "").splitlines():
        match = _PTS_RE.search(line)
        if match and "showinfo" in line:
            try:
                changes.append(float(match.group(1)))
            except ValueError:
                continue

    changes = sorted({round(t, 3) for t in changes if 0 < t < duration})
    return _assemble_scenes(changes, duration, min_duration, max_scenes)


def _assemble_scenes(
    changes: list[float],
    duration: float,
    min_duration: float,
    max_scenes: int,
) -> list[dict[str, Any]]:
    """Boundaries -> scene records, enforcing min duration and max count.

    Strategy (deterministic): start with boundary-based scenes, then merge
    the *shortest* scene into its neighbour until every scene satisfies
    ``min_duration``; finally merge the last scenes into one if the count
    still exceeds ``max_scenes``.
    """
    if duration <= 0:
        raise SceneDetectionError("Cannot build scenes: invalid video duration.")

    boundaries = [0.0] + changes + [duration]
    scenes: list[dict[str, Any]] = []
    for i in range(len(boundaries) - 1):
        start, end = boundaries[i], boundaries[i + 1]
        if end - start <= 0.001:
            continue
        scenes.append(_scene_record(len(scenes) + 1, start, end))

    # Enforce minimum scene duration (merge shortest into previous).
    while len(scenes) > 1:
        shortest = min(scenes, key=lambda s: s["duration"])
        if shortest["duration"] >= min_duration:
            break
        idx = scenes.index(shortest)
        if idx == 0:  # first scene: merge into the next one
            nxt = scenes[idx + 1]
            nxt["start"] = shortest["start"]
            nxt["duration"] = round(nxt["end"] - nxt["start"], 3)
        else:
            prev = scenes[idx - 1]
            prev["end"] = shortest["end"]
            prev["duration"] = round(prev["end"] - prev["start"], 3)
        scenes.pop(idx)
        _renumber(scenes)

    # Enforce maximum scene count (merge trailing scenes into the last one).
    while len(scenes) > max_scenes:
        last = scenes[-1]
        prev = scenes[-2]
        prev["end"] = last["end"]
        prev["duration"] = round(prev["end"] - prev["start"], 3)
        scenes.pop()
        _renumber(scenes)

    if not scenes:
        scenes = [_scene_record(1, 0.0, duration)]

    for scene in scenes:
        scene["representative_timestamp"] = round(
            (scene["start"] + scene["end"]) / 2.0, 3
        )
    logger.info("Scene detection: %d scene(s) from %d change(s).", len(scenes), len(changes))
    return scenes


def _scene_record(scene_id: int, start: float, end: float) -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 3),
    }


def _renumber(scenes: list[dict[str, Any]]) -> None:
    for i, scene in enumerate(scenes, start=1):
        scene["scene_id"] = i


def extract_representative_frames(
    ffmpeg_path: str,
    analysis_path: str | Path,
    frames_dir: str | Path,
    scenes: list[dict[str, Any]],
    *,
    analysis_fps: float,
    timeout_seconds: int,
    progress_callback: Callable[[float], None] | None = None,
) -> dict[int, str | None]:
    """Extract one JPEG per scene (single decode pass).

    Returns ``{scene_id: relative_path_or_None}`` - a missing frame never
    fails the pipeline (``None`` means the scene has no frame asset).
    """
    out_dir = Path(frames_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not scenes:
        return {}

    # Frame index for a timestamp: analysis copy has constant fps.
    total_frames = max(1, round(
        max(s["end"] for s in scenes) * analysis_fps
    ))
    indices: list[int] = []
    for scene in scenes:
        idx = min(total_frames - 1, max(0, round(scene["representative_timestamp"] * analysis_fps)))
        indices.append(idx)

    select_expr = "+".join(f"eq(n\\,{idx})" for idx in indices)
    flags: dict[str, object] = {}
    if os.name == "nt":  # pragma: no cover - Windows-only nicety
        flags["creationflags"] = subprocess.CREATE_NO_WINDOW
    output_pattern = str(out_dir / "scene_%03d.jpg")
    cmd = [
        ffmpeg_path, "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
        "-i", str(analysis_path),
        "-vf", f"select='{select_expr}',setpts=N/FRAME_RATE/TB",
        "-vsync", "vfr", "-start_number", "0",
        output_pattern,
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", **flags,  # type: ignore[arg-type]
        )
    except OSError as exc:
        raise FFmpegUnavailableError(f"Could not start FFmpeg (frame extraction): {exc}") from exc

    try:
        duration = max(s["end"] for s in scenes)
        _progress_lines(proc, duration, (0.0, 1.0), progress_callback)
        stderr = proc.stderr.read() if proc.stderr else ""
        deadline = time.monotonic() + timeout_seconds
        proc.wait(timeout=max(0.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        logger.error("Frame extraction timed out; continuing with partial frames.")
        stderr = ""
    if proc.returncode not in (0, None) and stderr:
        logger.warning("Frame extraction warning: %s", stderr.strip()[-300:])

    # image2 names outputs scene_000.jpg, scene_001.jpg ... in selection order.
    result: dict[int, str | None] = {}
    for scene in scenes:
        candidate = out_dir / f"scene_{scene['scene_id'] - 1:03d}.jpg"
        result[scene["scene_id"]] = (
            f"frames/{candidate.name}" if candidate.is_file() and candidate.stat().st_size > 0
            else None
        )
    missing = [sid for sid, rel in result.items() if rel is None]
    if missing:
        logger.warning("Representative frames missing for scenes: %s", missing)
    return result


__all__ = ["detect_scenes", "extract_representative_frames"]