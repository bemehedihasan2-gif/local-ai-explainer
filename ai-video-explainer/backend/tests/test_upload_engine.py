"""Phase 2: video upload & validation engine tests.

Runs against *fake* ffprobe binaries (see ``_media_helpers``) so the whole
engine is exercised deterministically even on machines without FFmpeg. Tests
that genuinely need real decoding live in ``test_upload_real_media.py``.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from _media_helpers import (
    install_fake_media_tools,
    probe_payload,
    sha256_bytes,
)
from app.database.connection import Database
from app.database.repositories.projects import ProjectRepository
from app.main import create_app
from app.services.ffmpeg import FfmpegService
from app.services.storage import StorageService
from app.services.uploads import UploadService

# ~33 KB of fake-but-storable content (never decoded by the fake prober).
VIDEO_BYTES = (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048 + b"\x00\x00\x00\x08free") * 16


@contextlib.contextmanager
def _client_with_media(settings, tmp_path: Path, **media_kwargs):
    """TestClient whose ffmpeg/ffprobe are fakes (kwargs -> install_fake_media_tools)."""
    ffmpeg_path, ffprobe_path = install_fake_media_tools(tmp_path, **media_kwargs)
    settings.ffmpeg_path = ffmpeg_path
    settings.ffprobe_path = ffprobe_path
    with TestClient(create_app(settings)) as client:
        yield client


def _post_video(
    client: TestClient,
    data: bytes = VIDEO_BYTES,
    filename: str = "clip.mp4",
    language: str = "en",
    target_duration: str = "180",
):
    return client.post(
        "/api/projects/upload",
        files={"file": (filename, data, "video/mp4")},
        data={"language": language, "target_duration": target_duration},
    )


@pytest.fixture
def client(settings, tmp_path):
    with _client_with_media(settings, tmp_path) as test_client:
        yield test_client, settings


# ----------------------------------------------------------------------
# Happy path + metadata
# ----------------------------------------------------------------------

def test_valid_mp4_upload_becomes_ready(client) -> None:
    test_client, _ = client
    response = _post_video(test_client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["progress"] == 100
    assert body["original_filename"] == "clip.mp4"
    assert body["language"] == "en"
    assert body["target_duration_seconds"] == 180
    assert body["error_message"] is None
    assert body["sha256"] == sha256_bytes(VIDEO_BYTES)
    assert body["file_size"] == len(VIDEO_BYTES)


def test_valid_mkv_extension_accepted(settings, tmp_path) -> None:
    with _client_with_media(
        settings, tmp_path,
        probe_body=probe_payload(format_name="matroska,webm"),
    ) as test_client:
        response = _post_video(test_client, filename="clip.mkv")
    assert response.status_code == 201, response.text
    assert response.json()["container_format"] == "matroska"


def test_metadata_extraction_duration_resolution_fps(client) -> None:
    test_client, _ = client
    body = _post_video(test_client).json()
    assert body["duration"] == 12.5
    assert body["width"] == 1920
    assert body["height"] == 1080
    assert body["fps"] == 29.97  # 30000/1001 normalized
    assert body["raw_fps"] == "30000/1001"  # raw rational preserved
    assert body["video_codec"] == "h264"
    assert body["audio_codec"] == "aac"
    assert body["container_format"] == "mov"
    assert body["bitrate"] == 1_500_000
    assert body["has_video"] is True
    assert body["has_audio"] is True


def test_fps_rational_parsing_covers_common_rates() -> None:
    from app.video.probe import _parse_fps

    assert _parse_fps("30000/1001") == 29.97
    assert _parse_fps("24000/1001") == 23.976
    assert _parse_fps("25/1") == 25.0
    assert _parse_fps("30/1") == 30.0
    assert _parse_fps("60/1") == 60.0
    assert _parse_fps("0/0") is None
    assert _parse_fps(None) is None


def test_video_without_audio_is_accepted(settings, tmp_path) -> None:
    with _client_with_media(
        settings, tmp_path,
        probe_body=probe_payload(with_audio=False),
    ) as test_client:
        response = _post_video(test_client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["has_audio"] is False
    assert body["audio_codec"] is None
    assert body["has_video"] is True


def test_response_never_leaks_internal_paths(client) -> None:
    test_client, _ = client
    response = _post_video(test_client)
    assert response.status_code == 201
    for forbidden in ("input_path", "stored_filename"):
        assert forbidden not in response.json()
    assert "data/projects" not in response.text


def test_project_api_returns_metadata_and_status(client) -> None:
    test_client, _ = client
    created = _post_video(test_client).json()
    response = test_client.get(f"/api/projects/{created['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["duration"] == created["duration"]
    assert body["sha256"] == created["sha256"]
    listed = test_client.get("/api/projects").json()
    assert any(p["id"] == created["id"] for p in listed)


# ----------------------------------------------------------------------
# Validation rejections
# ----------------------------------------------------------------------

def test_unsupported_extension_rejected(client) -> None:
    test_client, _ = client
    response = _post_video(test_client, filename="script.exe")
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "unsupported_file_type"
    assert "Supported video formats" in body["detail"]
    assert test_client.get("/api/projects").json() == []


def test_no_file_part_rejected(client) -> None:
    test_client, _ = client
    response = test_client.post(
        "/api/projects/upload",
        data={"language": "en", "target_duration": "180"},
    )
    assert response.status_code == 422


def test_empty_file_rejected_and_cleaned(client) -> None:
    test_client, settings = client
    response = _post_video(test_client, data=b"")
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_video"
    assert "empty" in body["detail"].lower()
    rows = test_client.get("/api/projects").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error_message"]
    assert not list(settings.projects_dir.iterdir())


def test_garbage_with_mp4_extension_rejected(settings, tmp_path) -> None:
    """Garbage bytes with a valid extension -> ffprobe rejects the file."""
    with _client_with_media(
        settings, tmp_path, probe_mode="exit_error"
    ) as test_client:
        response = _post_video(test_client, data=b"definitely not a video" * 100)
        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "invalid_video"
        assert "ffprobe" in body["detail"].lower()
        # FAILED row exists; no partial file is left behind.
        rows = test_client.get("/api/projects").json()
        assert rows[0]["status"] == "failed"
    assert not list(settings.projects_dir.iterdir())


def test_corrupt_streams_json_rejected(settings, tmp_path) -> None:
    """FFprobe returns 'no streams' for a garbage container."""
    with _client_with_media(
        settings, tmp_path, probe_mode="empty_streams"
    ) as test_client:
        response = _post_video(test_client)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_video"
    assert not list(settings.projects_dir.iterdir())


def test_audio_only_file_rejected(settings, tmp_path) -> None:
    with _client_with_media(
        settings, tmp_path,
        probe_body=probe_payload(no_video_stream=True),
    ) as test_client:
        response = _post_video(test_client, filename="audio.mp4")
    assert response.status_code == 422
    assert "no video stream" in response.json()["detail"].lower()


def test_invalid_language_rejected(client) -> None:
    test_client, _ = client
    response = _post_video(test_client, language="fr")
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_parameter"
    assert "en, hi, bn" in body["detail"]


def test_invalid_target_duration_rejected(client) -> None:
    test_client, _ = client
    for duration in ("90", "300", "abc"):
        response = _post_video(test_client, target_duration=duration)
        assert response.status_code == 422, duration
        assert response.json()["error"] == "invalid_parameter"
    assert test_client.get("/api/projects").json() == []


def test_oversized_upload_rejected_and_cleaned(client) -> None:
    test_client, settings = client
    settings.max_upload_size_mb = 1  # enforce a ~1 MB cap
    response = _post_video(test_client, data=b"x" * (2 * 1024 * 1024 + 100))
    assert response.status_code == 413
    body = response.json()
    assert body["error"] == "upload_too_large"
    assert "maximum allowed upload size" in body["detail"]
    rows = test_client.get("/api/projects").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert "maximum allowed upload size" in rows[0]["error_message"]
    assert not list(settings.projects_dir.iterdir())


def test_ffprobe_unavailable_returns_clean_error(settings, tmp_path) -> None:
    settings.ffmpeg_path = tmp_path / "missing" / "ffmpeg"
    settings.ffprobe_path = tmp_path / "missing" / "ffprobe"
    with TestClient(create_app(settings)) as test_client:
        response = _post_video(test_client)
        assert response.status_code == 503
        body = response.json()
        assert body["error"] == "ffmpeg_unavailable"
        assert "Install FFmpeg" in body["detail"]
        assert test_client.get("/api/projects").json() == []
    assert not list(settings.projects_dir.iterdir())


# ----------------------------------------------------------------------
# Streaming / hashing / duplicates / cleanup
# ----------------------------------------------------------------------

def test_uploaded_bytes_are_stored_identically(client) -> None:
    test_client, settings = client
    body = _post_video(test_client).json()
    project_dir = settings.projects_dir / body["id"]
    stored = list((project_dir / "input").iterdir())
    assert len(stored) == 1
    assert stored[0].read_bytes() == VIDEO_BYTES
    assert stored[0].name == f"{body['id'][:16]}.mp4"  # generated internal name
    assert (project_dir / "temp").is_dir()
    assert (project_dir / "output").is_dir()


def test_duplicate_upload_detected_409(client) -> None:
    test_client, settings = client
    first = _post_video(test_client)
    assert first.status_code == 201
    response = _post_video(test_client, filename="copy-of-same.mp4")
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "duplicate_video"
    assert "duplicate" in body["detail"].lower()
    projects = test_client.get("/api/projects").json()
    assert len(projects) == 1  # throwaway upload removed
    assert projects[0]["id"] == first.json()["id"]
    assert (settings.projects_dir / first.json()["id"]).is_dir()


def test_retry_after_failed_upload_is_not_duplicate(settings, tmp_path) -> None:
    with _client_with_media(settings, tmp_path, probe_mode="exit_error") as c1:
        assert _post_video(c1).status_code == 422
        projects_dir = settings.projects_dir
        assert not list(projects_dir.iterdir())  # first attempt fully cleaned
    # Same bytes are accepted once the prober works -> not flagged duplicate.
    with _client_with_media(settings, tmp_path) as c2:
        response = _post_video(c2, filename="retry.mp4")
        assert response.status_code == 201
        assert c2.get("/api/projects").json()[0]["status"] == "ready"


def test_path_traversal_filename_is_neutralized(client) -> None:
    test_client, settings = client
    response = _post_video(test_client, filename="..\\..\\..\\evil.mp4")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["original_filename"] == "evil.mp4"
    assert "\\" not in body["original_filename"] and "/" not in body["original_filename"]
    stored = list((settings.projects_dir / body["id"] / "input").iterdir())
    assert stored[0].read_bytes() == VIDEO_BYTES


def test_delete_project_removes_db_record_and_files(client) -> None:
    test_client, settings = client
    body = _post_video(test_client).json()
    project_dir = settings.projects_dir / body["id"]
    assert project_dir.is_dir()
    response = test_client.delete(f"/api/projects/{body['id']}")
    assert response.status_code == 204
    assert test_client.get(f"/api/projects/{body['id']}").status_code == 404
    assert not project_dir.exists()
    assert test_client.get("/api/projects").json() == []


def test_upload_progress_is_honest_and_sequential(settings, tmp_path) -> None:
    ffmpeg_path, ffprobe_path = install_fake_media_tools(tmp_path)
    settings.ffmpeg_path = ffmpeg_path
    settings.ffprobe_path = ffprobe_path
    settings.upload_chunk_size = 64 * 1024
    db = Database(settings.database_path).initialize()
    uploader = UploadService(settings, db, FfmpegService(settings), StorageService(settings))
    data = bytes(range(256)) * (4 * 1024)  # 1 MiB deterministic

    import io

    class FakeUpload:
        filename = "clip.mp4"
        size = len(data)
        file = io.BytesIO(data)

    seen: list[float] = []
    row = uploader.handle_upload(
        upload_file=FakeUpload(),
        original_filename="clip.mp4",
        language="en",
        target_duration_seconds=120,
        progress_callback=seen.append,
    )
    assert row["status"] == "ready"
    assert row["progress"] == 100
    assert len(seen) >= 3, f"expected several progress ticks, got {seen}"
    assert seen == sorted(seen)  # never goes backwards
    assert seen[0] == 0  # project creation reports 0
    assert seen[-1] == 100
    assert all(0 <= value < 100 for value in seen[:-1])
    assert any(0 < value <= 90 for value in seen)  # byte-based upload capped at 90


def test_schema_migration_preserves_phase1_rows(settings) -> None:
    """A Phase 1-era database gains new columns without data loss."""
    old_db_path = settings.database_path
    old_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(old_db_path))
    conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            original_filename TEXT NOT NULL,
            stored_filename TEXT,
            input_path TEXT,
            duration REAL,
            width INTEGER,
            height INTEGER,
            fps REAL,
            language TEXT NOT NULL DEFAULT 'en',
            target_duration_seconds INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'created',
            progress REAL NOT NULL DEFAULT 0,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO projects (id, original_filename, target_duration_seconds, "
        "created_at, updated_at) VALUES ('legacy-1', 'old.mp4', 180, 't', 't')"
    )
    conn.commit()
    conn.close()

    db = Database(old_db_path).initialize()  # idempotent + migrates
    repo = ProjectRepository(db)
    row = repo.get("legacy-1")
    assert row["original_filename"] == "old.mp4"  # data preserved
    for column in ("sha256", "file_size", "raw_fps", "video_codec", "audio_codec",
                   "container_format", "bitrate", "has_video", "has_audio"):
        assert column in row
    new = repo.create(
        original_filename="new.mp4", language="en", target_duration_seconds=120,
        status="uploading",
    )
    assert new["id"] != "legacy-1"
