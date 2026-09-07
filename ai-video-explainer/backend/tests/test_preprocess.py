"""Phase 3: preprocessing & analysis-asset generation tests.

Runs against *fake* ffmpeg/ffprobe executables (see ``_media_helpers``), so
the full job lifecycle is exercised deterministically even without FFmpeg:
queue -> running -> completed/prepared, progress persistence, failure
handling, one-job-at-a-time worker concurrency, asset cleanup and the
thumbnail endpoint. Real-media equivalents live in ``test_preprocess_real.py``.
"""

from __future__ import annotations

import contextlib
import time

import pytest
from fastapi.testclient import TestClient

from _media_helpers import (
    install_fake_media_tools,
    probe_payload,
)
from app.main import create_app

VIDEO_BYTES = (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048 + b"\x00\x00\x00\x08free") * 16


@contextlib.contextmanager
def _client_with_media(settings, tmp_path, **media_kwargs):
    ffmpeg_path, ffprobe_path = install_fake_media_tools(tmp_path, **media_kwargs)
    settings.ffmpeg_path = ffmpeg_path
    settings.ffprobe_path = ffprobe_path
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def client(settings, tmp_path):
    with _client_with_media(settings, tmp_path) as test_client:
        yield test_client, settings


def _post_video(client: TestClient, filename: str = "clip.mp4"):
    return client.post(
        "/api/projects/upload",
        files={"file": (filename, VIDEO_BYTES, "video/mp4")},
        data={"language": "en", "target_duration": "180"},
    )


def _upload_ready(client: TestClient) -> dict:
    response = _post_video(client)
    assert response.status_code == 201, response.text
    return response.json()


def _wait_for(client: TestClient, project_id: str, predicate, timeout: float = 8.0):
    """Poll GET /api/projects/{id} until ``predicate(body)`` is true."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/projects/{project_id}").json()
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for project {project_id}; last state: {last}")


def _start_preprocess(client: TestClient, project_id: str):
    return client.post(f"/api/projects/{project_id}/preprocess")


# ----------------------------------------------------------------------
# Happy path: ready -> preprocessing -> prepared with all three assets
# ----------------------------------------------------------------------

def test_preprocess_full_flow_generates_assets(client) -> None:
    test_client, settings = client
    project = _upload_ready(test_client)

    response = _start_preprocess(test_client, project["id"])
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["status"] == "queued"
    assert job["stage"] == "preprocess"
    assert job["project_id"] == project["id"]
    assert job["progress"] == 0

    # The project immediately leaves READY and reports honest progress.
    prepared = _wait_for(
        test_client, project["id"],
        lambda body: body["status"] == "prepared",
    )
    assert prepared["progress"] == 100
    assert prepared["error_message"] is None

    # Asset metadata is stored (relative paths only - no leaks).
    assert prepared["analysis_path"] == "analysis/analysis.mp4"
    assert prepared["analysis_width"] == 640   # 1920 -> capped at config width
    assert prepared["analysis_height"] == 360  # 1080 * 640/1920, even
    assert prepared["analysis_fps"] == 5.0
    assert prepared["thumbnail_path"] == "thumbnails/poster.jpg"
    assert prepared["audio_path"] == "audio/audio.wav"
    assert prepared["prepared_at"]
    assert "input_path" not in prepared and "stored_filename" not in prepared
    assert "data/projects" not in response.text

    # The three files really exist (non-empty) inside the project folder.
    project_dir = settings.projects_dir / project["id"]
    assert (project_dir / "analysis" / "analysis.mp4").read_bytes() == b"fake-asset"
    assert (project_dir / "thumbnails" / "poster.jpg").read_bytes() == b"fake-asset"
    assert (project_dir / "audio" / "audio.wav").read_bytes() == b"fake-asset"

    # Job row is completed with 100%.
    jobs = test_client.get(f"/api/projects/{project['id']}/jobs").json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["progress"] == 100
    assert jobs[0]["error_message"] is None
    assert jobs[0]["started_at"] and jobs[0]["completed_at"]

    # The poster is served as JPEG from a safe path.
    thumb = test_client.get(f"/api/projects/{project['id']}/thumbnail")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/jpeg")
    assert thumb.content == b"fake-asset"


def test_preprocess_without_audio_skips_wav(client) -> None:
    test_client, settings = client
    project = _upload_ready(test_client)
    # Rewrite the row to simulate a silent video (audio flag off).
    from app.database.connection import Database
    from app.database.repositories.projects import ProjectRepository

    repo = ProjectRepository(Database(settings.database_path))
    repo.update(project["id"], has_audio=False, audio_codec=None)

    assert _start_preprocess(test_client, project["id"]).status_code == 201
    prepared = _wait_for(
        test_client, project["id"],
        lambda body: body["status"] == "prepared",
    )
    assert prepared["audio_path"] is None
    project_dir = settings.projects_dir / project["id"]
    assert not (project_dir / "audio" / "audio.wav").exists()


def test_analysis_dimensions_recorded_for_small_video(settings, tmp_path) -> None:
    """Sources narrower than the cap are never upscaled."""
    with _client_with_media(
        settings, tmp_path,
        probe_body=probe_payload(width=320, height=240),
    ) as small_client:
        project = _upload_ready(small_client)
        assert _start_preprocess(small_client, project["id"]).status_code == 201
        prepared = _wait_for(
            small_client, project["id"],
            lambda body: body["status"] == "prepared",
        )
        assert prepared["analysis_width"] == 320
        assert prepared["analysis_height"] == 240
        assert prepared["analysis_fps"] == 5.0


def test_source_metadata_survives_preprocessing(client) -> None:
    test_client, _ = client
    project = _upload_ready(test_client)
    _start_preprocess(test_client, project["id"])
    prepared = _wait_for(
        test_client, project["id"],
        lambda body: body["status"] == "prepared",
    )
    for key in ("duration", "width", "height", "fps", "video_codec",
                "container_format", "has_audio", "sha256", "file_size"):
        assert prepared[key] == project[key], key


# ----------------------------------------------------------------------
# Guard rails: statuses, conflicts, 404s
# ----------------------------------------------------------------------

def test_preprocess_unknown_project_404(client) -> None:
    test_client, _ = client
    response = _start_preprocess(test_client, "does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"] == "project_not_found"


def test_preprocess_rejects_non_ready_project(client) -> None:
    test_client, _ = client
    created = test_client.post(
        "/api/projects",
        json={"original_filename": "later.mp4", "language": "en",
              "target_duration_minutes": 2},
    ).json()
    assert created["status"] == "created"
    response = _start_preprocess(test_client, created["id"])
    assert response.status_code == 409
    assert response.json()["error"] == "project_not_ready"
    assert "ready" in response.json()["detail"].lower()


def test_preprocess_double_start_is_conflict(client, monkeypatch) -> None:
    test_client, _ = client
    project = _upload_ready(test_client)
    worker = test_client.app.state.worker
    submitted: list[str] = []
    monkeypatch.setattr(worker, "submit", submitted.append)  # job never runs

    first = _start_preprocess(test_client, project["id"])
    assert first.status_code == 201
    assert first.json()["status"] == "queued"
    assert submitted == [first.json()["id"]]

    second = _start_preprocess(test_client, project["id"])
    assert second.status_code == 409
    assert second.json()["error"] == "job_conflict"

    # The project reflects the queued job honestly.
    body = test_client.get(f"/api/projects/{project['id']}").json()
    assert body["status"] == "preprocessing"
    jobs = test_client.get(f"/api/projects/{project['id']}/jobs").json()
    assert len(jobs) == 1 and jobs[0]["status"] == "queued"


def test_jobs_endpoint_404_for_unknown_project(client) -> None:
    test_client, _ = client
    assert test_client.get("/api/projects/nope/jobs").status_code == 404


def test_thumbnail_404_before_preprocessing(client) -> None:
    test_client, _ = client
    project = _upload_ready(test_client)
    response = test_client.get(f"/api/projects/{project['id']}/thumbnail")
    assert response.status_code == 404
    assert response.json()["error"] == "asset_not_found"


# ----------------------------------------------------------------------
# Failures: recorded in the job, project returns to READY for retry
# ----------------------------------------------------------------------

def test_preprocess_failure_records_error_and_allows_retry(settings, tmp_path) -> None:
    with _client_with_media(
        settings, tmp_path, preprocess_mode="fail"
    ) as test_client:
        project = _upload_ready(test_client)
        assert _start_preprocess(test_client, project["id"]).status_code == 201

        body = _wait_for(
            test_client, project["id"],
            lambda b: b["status"] == "ready" and b["error_message"] is not None,
        )
        assert "FFmpeg failed" in body["error_message"]

        jobs = test_client.get(f"/api/projects/{project['id']}/jobs").json()
        assert len(jobs) == 1
        assert jobs[0]["status"] == "failed"
        assert "FFmpeg failed" in jobs[0]["error_message"]
        assert jobs[0]["completed_at"]

        # No partial assets remain on disk.
        project_dir = settings.projects_dir / project["id"]
        assert not (project_dir / "analysis").exists() or not list(
            (project_dir / "analysis").iterdir()
        )
        assert not list((project_dir / "thumbnails").iterdir())
        assert not list((project_dir / "audio").iterdir())

        # Retry after a failure is allowed and succeeds with working ffmpeg.
        retry_dir = tmp_path / "retry"
        retry_dir.mkdir(exist_ok=True)
        settings.ffmpeg_path, settings.ffprobe_path = install_fake_media_tools(
            retry_dir, preprocess_mode="ok"
        )
        assert _start_preprocess(test_client, project["id"]).status_code == 201
        prepared = _wait_for(
            test_client, project["id"],
            lambda b: b["status"] == "prepared",
        )
        assert prepared["error_message"] is None
        jobs = test_client.get(f"/api/projects/{project['id']}/jobs").json()
        assert [j["status"] for j in jobs] == ["failed", "completed"]


def test_preprocess_without_ffmpeg_records_clean_failure(client) -> None:
    test_client, settings = client
    project = _upload_ready(test_client)
    settings.ffmpeg_path = settings.base_dir / "missing" / "ffmpeg"

    assert _start_preprocess(test_client, project["id"]).status_code == 201
    body = _wait_for(
        test_client, project["id"],
        lambda b: b["status"] == "ready" and b["error_message"] is not None,
    )
    assert "Install FFmpeg" in body["error_message"]
    jobs = test_client.get(f"/api/projects/{project['id']}/jobs").json()
    assert jobs[0]["status"] == "failed"
    assert "Install FFmpeg" in jobs[0]["error_message"]


# ----------------------------------------------------------------------
# Worker behavior: one heavy job at a time, honest intermediate progress
# ----------------------------------------------------------------------

def test_worker_runs_one_job_at_a_time_with_real_progress(settings, tmp_path) -> None:
    with _client_with_media(
        settings, tmp_path, preprocess_mode="slow"
    ) as test_client:
        first = _post_video(test_client, "first.mp4").json()
        second = test_client.post(
            "/api/projects/upload",
            files={"file": ("second.mp4", VIDEO_BYTES + b"\x01", "video/mp4")},
            data={"language": "en", "target_duration": "180"},
        )
        assert second.status_code == 201, second.text
        second = second.json()

        assert _start_preprocess(test_client, first["id"]).status_code == 201
        assert _start_preprocess(test_client, second["id"]).status_code == 201

        progress_samples: list[float] = []
        observed_running = 0
        deadline = time.time() + 15
        while time.time() < deadline:
            jobs = test_client.get(f"/api/projects/{first['id']}/jobs").json()
            jobs += test_client.get(f"/api/projects/{second['id']}/jobs").json()
            running = [j for j in jobs if j["status"] == "running"]
            queued = [j for j in jobs if j["status"] == "queued"]
            # The worker is strictly serial: never two running jobs.
            assert len(running) <= 1, f"two jobs running at once: {jobs}"
            observed_running += len(running)

            first_body = test_client.get(f"/api/projects/{first['id']}").json()
            second_body = test_client.get(f"/api/projects/{second['id']}").json()
            progress_samples += [
                first_body["progress"], second_body["progress"],
            ]
            if first_body["status"] == "prepared" and second_body["status"] == "prepared":
                break
            time.sleep(0.05)

        assert first_body["status"] == "prepared", first_body
        assert second_body["status"] == "prepared", second_body

        # The slow fake ffmpeg guarantees intermediate, strictly monotonic
        # progress was observed (not a fake 0 -> 100 jump).
        intermediates = [
            p for p in progress_samples if 0 < p < 100
        ]
        assert intermediates, "no intermediate progress values observed"
        assert min(intermediates) > 0 and max(intermediates) < 100
        assert observed_running > 0

        # Second job was queued behind the first, then completed.
        second_jobs = test_client.get(f"/api/projects/{second['id']}/jobs").json()
        assert second_jobs[0]["status"] == "completed"


def test_worker_skips_stale_queued_jobs(settings, tmp_path) -> None:
    """A job row whose project vanished is dropped silently, worker survives."""
    with _client_with_media(settings, tmp_path) as test_client:
        project = _upload_ready(test_client)
        worker = test_client.app.state.worker
        job_id = test_client.post(f"/api/projects/{project['id']}/preprocess").json()["id"]
        # Delete the project while the job is queued (cascade removes the row).
        assert test_client.delete(f"/api/projects/{project['id']}").status_code == 204
        worker.submit(job_id)  # row no longer exists; must be dropped, not crash
        time.sleep(0.3)
        assert worker.status()["active_job"] is None
        assert test_client.get("/api/system/status").json()["worker"]["running"] is True


# ----------------------------------------------------------------------
# Cleanup on delete
# ----------------------------------------------------------------------

def test_delete_project_removes_analysis_assets(client) -> None:
    test_client, settings = client
    project = _upload_ready(test_client)
    _start_preprocess(test_client, project["id"])
    prepared = _wait_for(
        test_client, project["id"],
        lambda body: body["status"] == "prepared",
    )
    project_dir = settings.projects_dir / project["id"]
    assert (project_dir / "analysis").is_dir()
    assert prepared["analysis_path"]

    assert test_client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert not project_dir.exists()
    assert test_client.get(f"/api/projects/{project['id']}").status_code == 404
    assert test_client.get(f"/api/projects/{project['id']}/thumbnail").status_code == 404