"""Phase 4: local analysis pipeline tests.

Runs against *fake* ffmpeg/ffprobe executables (see ``_media_helpers``), so
the full pipeline is exercised deterministically even without FFmpeg:

    PREPARED -> scene detection -> STT (graceful) -> OCR (graceful) ->
    visual -> timeline -> quality check -> ANALYZED

STT and OCR are patched to their graceful "unavailable" paths so results
are identical on every machine (no Whisper model / Tesseract needed); the
positive paths are unit-tested with fakes at the service level. Real-media
integration lives in ``test_analysis_real.py``.
"""

from __future__ import annotations

import contextlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from _media_helpers import install_fake_media_tools
from app.main import create_app
from app.utils.errors import SceneDetectionError

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


@pytest.fixture
def deterministic_ai(monkeypatch):
    """Force graceful STT/OCR absence: identical results on any machine."""
    monkeypatch.setattr("app.ai.stt.whisper_model_installed", lambda settings: False)
    monkeypatch.setattr("app.ai.ocr.tesseract_available", lambda settings: False)
    return None


def _post_video(test_client, *, payload: bytes = VIDEO_BYTES, filename: str = "clip.mp4"):
    return test_client.post(
        "/api/projects/upload",
        files={"file": (filename, payload, "video/mp4")},
        data={"language": "en", "target_duration": "180"},
    )


def _wait_for(test_client, project_id, predicate, timeout: float = 15.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = test_client.get(f"/api/projects/{project_id}").json()
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for project {project_id}; last state: {last}")


def _upload_prepared(test_client, *, payload: bytes = VIDEO_BYTES) -> dict:
    response = _post_video(test_client, payload=payload)
    assert response.status_code == 201, response.text
    project = response.json()
    assert test_client.post(f"/api/projects/{project['id']}/preprocess").status_code == 201
    return _wait_for(
        test_client, project["id"], lambda body: body["status"] == "prepared"
    )


def _analyze_to_analyzed(test_client, project_id: str) -> dict:
    response = test_client.post(f"/api/projects/{project_id}/analyze")
    assert response.status_code == 200, response.text
    assert response.json()["idempotent"] is False
    assert response.json()["status"] == "analyzing"
    assert response.json()["analysis_id"]
    assert response.json()["job"]["stage"] == "analysis"
    return _wait_for(
        test_client, project_id, lambda body: body["status"] == "analyzed"
    )


def _assert_no_absolute_paths(document, settings_base: str) -> None:
    def walk(node) -> None:
        if isinstance(node, str):
            assert settings_base not in node, f"absolute path leaked: {node!r}"
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)


# ----------------------------------------------------------------------
# Happy path: prepared -> analyzing -> analyzed with structured artifacts
# ----------------------------------------------------------------------

def test_analysis_full_flow_to_analyzed(client, deterministic_ai) -> None:
    test_client, settings = client
    prepared = _upload_prepared(test_client)
    project_id = prepared["id"]

    analyzed = _analyze_to_analyzed(test_client, project_id)
    assert analyzed["progress"] == 100
    assert analyzed["error_message"] is None

    # Run summary in SQLite (no transcript/OCR payload blobs - JSON files).
    run = test_client.get(f"/api/projects/{project_id}/analysis").json()
    assert run["status"] == "completed"
    assert run["scene_count"] == 3  # fake ffmpeg reports changes at 3.2s and 7.8s
    assert run["transcript_available"] is False      # Whisper model absent
    assert run["ocr_available"] is False             # Tesseract absent
    assert run["visual_provider"] == "deterministic"
    assert run["processing_seconds"] > 0
    assert len(run["warnings"]) >= 2
    assert any("whisper" in w.lower() or "model" in w.lower() for w in run["warnings"])
    assert any("tesseract" in w.lower() or "ocr" in w.lower() for w in run["warnings"])
    assert run["started_at"] and run["completed_at"]

    # Jobs are persisted and complete in order.
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs] == ["preprocess", "analysis"]
    assert jobs[-1]["status"] == "completed"
    assert jobs[-1]["progress"] == 100

    # Artifacts on disk, relative paths only.
    metadata = settings.projects_dir / project_id / "analysis" / "metadata"
    frames = settings.projects_dir / project_id / "analysis" / "frames"
    scenes_doc = json.loads((metadata / "scenes.json").read_text(encoding="utf-8"))
    assert scenes_doc["schema_version"] == 1
    assert len(scenes_doc["scenes"]) == 3
    for scene in scenes_doc["scenes"]:
        assert scene["end"] > scene["start"]
        assert scene["duration"] > 0
        assert 0 <= scene["start"] < scene["end"] <= 12.5
        assert scene["start"] <= scene["representative_timestamp"] <= scene["end"]

    visual_doc = json.loads((metadata / "visual.json").read_text(encoding="utf-8"))
    assert visual_doc["provider"] == "deterministic"
    assert len(visual_doc["frames"]) == 3
    assert (frames / "scene_000.jpg").is_file()
    assert (frames / "scene_001.jpg").stat().st_size > 0
    # Graceful absence: no fake transcript/ocr documents are written.
    assert not (metadata / "transcript.json").exists()
    assert not (metadata / "ocr.json").exists()
    assert (metadata / "timeline.json").is_file()
    assert (metadata / "analysis_manifest.json").is_file()

    # Timeline endpoint: aligned scenes with per-scene evidence.
    timeline = test_client.get(f"/api/projects/{project_id}/timeline")
    assert timeline.status_code == 200
    doc = timeline.json()
    assert doc["schema_version"] == 1
    assert doc["summary"]["scene_count"] == 3
    assert doc["summary"]["speech_scenes"] == 0
    assert doc["summary"]["ocr_scenes"] == 0
    scene = doc["scenes"][0]
    assert scene["speech_present"] is False
    assert scene["ocr_present"] is False
    assert scene["visual"] is not None
    assert scene["visual"]["width"] == 64
    assert 0 <= scene["information_density"] <= 100
    assert scene["representative_frame"] == "frames/scene_000.jpg"
    assert "data/projects" not in timeline.text

    # Representative-frame endpoint (path-safe).
    frame = test_client.get(f"/api/projects/{project_id}/analysis/frames/1")
    assert frame.status_code == 200
    assert frame.headers["content-type"].startswith("image/jpeg")
    assert len(frame.content) > 0
    assert test_client.get(f"/api/projects/{project_id}/analysis/frames/999").status_code == 404

    # Manifest: structured, fingerprint-linked, and path-safe.
    manifest = json.loads((metadata / "analysis_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["results"]["scene_count"] == 3
    assert manifest["results"]["transcript_available"] is False
    assert manifest["results"]["visual_provider"] == "deterministic"
    assert manifest["source"]["sha256"]
    assert manifest["preprocessing"]["analysis_path"] == "analysis/analysis.mp4"
    assert manifest["results"]["assets"]["timeline"] == "analysis/metadata/timeline.json"
    _assert_no_absolute_paths(manifest, str(settings.base_dir))
    assert str(settings.projects_dir) not in json.dumps(manifest)


def test_analysis_manifest_and_run_have_no_internal_paths(client, deterministic_ai) -> None:
    """API responses must never expose server filesystem paths."""
    test_client, settings = client
    prepared = _upload_prepared(test_client)
    project_id = prepared["id"]
    _analyze_to_analyzed(test_client, project_id)

    for endpoint in (
        f"/api/projects/{project_id}",
        f"/api/projects/{project_id}/analysis",
        f"/api/projects/{project_id}/timeline",
        f"/api/projects/{project_id}/jobs",
    ):
        text = test_client.get(endpoint).text
        assert str(settings.base_dir) not in text, endpoint
        assert str(settings.projects_dir) not in text, endpoint


# ----------------------------------------------------------------------
# Guard rails: statuses, conflicts, 404s
# ----------------------------------------------------------------------

def test_analyze_unknown_project_404(client) -> None:
    test_client, _ = client
    response = test_client.post("/api/projects/does-not-exist/analyze")
    assert response.status_code == 404
    assert response.json()["error"] == "project_not_found"


def test_analyze_rejects_ready_project(client, deterministic_ai) -> None:
    test_client, _ = client
    project = _post_video(test_client).json()
    assert project["status"] == "ready"
    response = test_client.post(f"/api/projects/{project['id']}/analyze")
    assert response.status_code == 409
    assert response.json()["error"] == "analysis_not_ready"
    assert "preprocess" in response.json()["detail"].lower()


def test_analyze_rejects_created_record_only_project(client, deterministic_ai) -> None:
    test_client, _ = client
    created = test_client.post(
        "/api/projects",
        json={"original_filename": "later.mp4", "language": "en",
              "target_duration_minutes": 2},
    ).json()
    response = test_client.post(f"/api/projects/{created['id']}/analyze")
    assert response.status_code == 409
    assert response.json()["error"] == "analysis_not_ready"


def test_analyze_conflict_when_already_running(client, deterministic_ai, monkeypatch) -> None:
    test_client, _ = client
    prepared = _upload_prepared(test_client)
    project_id = prepared["id"]
    worker = test_client.app.state.worker
    submitted: list[str] = []
    monkeypatch.setattr(worker, "submit", submitted.append)  # job never runs

    first = test_client.post(f"/api/projects/{project_id}/analyze")
    assert first.status_code == 200
    assert submitted, "job must be persisted and submitted"
    body = test_client.get(f"/api/projects/{project_id}").json()
    assert body["status"] == "analyzing"

    second = test_client.post(f"/api/projects/{project_id}/analyze")
    assert second.status_code == 409
    assert second.json()["error"] == "analysis_already_running"

    # Exactly one analysis job row was persisted.
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs] == ["preprocess", "analysis"]
    assert jobs[-1]["status"] == "queued"


def test_analyze_stale_running_state_recovers(settings, tmp_path, monkeypatch) -> None:
    """A project stuck in ANALYZING after a crash (rows gone) resets to PREPARED."""
    with _client_with_media(settings, tmp_path) as test_client:
        prepared = _upload_prepared(test_client)
        project_id = prepared["id"]
        monkeypatch.setattr(
            test_client.app.state.worker, "submit", lambda job_id: None
        )
        assert test_client.post(f"/api/projects/{project_id}/analyze").status_code == 200
        assert test_client.get(f"/api/projects/{project_id}").json()["status"] == "analyzing"

        # Simulate the crash: both job + run rows vanished without a worker.
        from app.database.connection import Database

        db = Database(settings.database_path)
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM processing_jobs WHERE project_id = ?", (project_id,)
            )
            conn.execute(
                "DELETE FROM analysis_results WHERE project_id = ?", (project_id,)
            )

        monkeypatch.undo()  # real submit again
        response = test_client.post(f"/api/projects/{project_id}/analyze")
        assert response.status_code == 200
        assert response.json()["idempotent"] is False
        analyzed = _wait_for(
            test_client, project_id, lambda body: body["status"] == "analyzed"
        )
        assert analyzed["error_message"] is None


# ----------------------------------------------------------------------
# Idempotency & configuration invalidation
# ----------------------------------------------------------------------

def test_analyzed_results_are_reused_idempotently(client, deterministic_ai) -> None:
    test_client, _ = client
    prepared = _upload_prepared(test_client)
    project_id = prepared["id"]
    _analyze_to_analyzed(test_client, project_id)
    run_before = test_client.get(f"/api/projects/{project_id}/analysis").json()
    jobs_before = test_client.get(f"/api/projects/{project_id}/jobs").json()

    response = test_client.post(f"/api/projects/{project_id}/analyze")
    assert response.status_code == 200
    body = response.json()
    assert body["idempotent"] is True
    assert body["status"] == "analyzed"
    assert body["analysis"]["id"] == run_before["id"]

    # Nothing was re-run: no new job, no new run row.
    assert test_client.get(f"/api/projects/{project_id}/jobs").json() == jobs_before
    assert test_client.get(f"/api/projects/{project_id}/analysis").json()["id"] == run_before["id"]


def test_config_change_invalidates_previous_results(client, deterministic_ai) -> None:
    test_client, settings = client
    prepared = _upload_prepared(test_client)
    project_id = prepared["id"]
    _analyze_to_analyzed(test_client, project_id)

    settings.scene_threshold = 0.9  # any analysis-relevant change
    response = test_client.post(f"/api/projects/{project_id}/analyze")
    assert response.status_code == 200
    assert response.json()["idempotent"] is False

    _wait_for(test_client, project_id, lambda body: body["status"] == "analyzed")
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs] == ["preprocess", "analysis", "analysis"]


# ----------------------------------------------------------------------
# Silent videos, failures, retry & cleanup
# ----------------------------------------------------------------------

def test_silent_video_analysis_skips_stt(client, deterministic_ai) -> None:
    test_client, settings = client
    project = _post_video(test_client).json()

    from app.database.connection import Database
    from app.database.repositories.projects import ProjectRepository

    ProjectRepository(Database(settings.database_path)).update(
        project["id"], has_audio=False, audio_codec=None
    )
    assert test_client.post(f"/api/projects/{project['id']}/preprocess").status_code == 201
    prepared = _wait_for(
        test_client, project["id"], lambda body: body["status"] == "prepared"
    )
    assert prepared["audio_path"] is None

    _analyze_to_analyzed(test_client, project["id"])
    run = test_client.get(f"/api/projects/{project['id']}/analysis").json()
    assert run["transcript_available"] is False
    assert any("no audio" in w.lower() for w in run["warnings"])
    # The rest of the pipeline still ran.
    assert run["scene_count"] == 3
    assert run["visual_provider"] == "deterministic"


def test_analysis_failure_records_error_cleans_partials_and_allows_retry(
    settings, tmp_path, monkeypatch, deterministic_ai
) -> None:
    with _client_with_media(settings, tmp_path) as test_client:
        prepared = _upload_prepared(test_client)
        project_id = prepared["id"]
        metadata = settings.projects_dir / project_id / "analysis" / "metadata"

        from app.video.scenes import detect_scenes as real_detect

        def boom(*args, **kwargs):
            raise SceneDetectionError("simulated scene detection failure")

        monkeypatch.setattr("app.services.analysis.detect_scenes", boom)
        assert test_client.post(f"/api/projects/{project_id}/analyze").status_code == 200
        body = _wait_for(
            test_client, project_id,
            lambda b: b["status"] == "prepared" and b["error_message"] is not None,
        )
        assert "scene" in body["error_message"].lower()

        jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
        assert jobs[-1]["stage"] == "analysis"
        assert jobs[-1]["status"] == "failed"
        assert jobs[-1]["error_message"]
        run = test_client.get(f"/api/projects/{project_id}/analysis").json()
        assert run["status"] == "failed"
        assert run["error_message"]

        # Partial Phase 4 artifacts removed; Phase 3 assets preserved.
        assert not metadata.exists() or not list(metadata.iterdir())
        analysis_video = settings.projects_dir / project_id / "analysis" / "analysis.mp4"
        assert analysis_video.is_file()
        assert (
            settings.projects_dir / project_id / "thumbnails" / "poster.jpg"
        ).is_file()

        # Retry succeeds without re-uploading or re-preprocessing.
        monkeypatch.setattr("app.services.analysis.detect_scenes", real_detect)
        assert test_client.post(f"/api/projects/{project_id}/analyze").status_code == 200
        analyzed = _wait_for(
            test_client, project_id, lambda body: body["status"] == "analyzed"
        )
        assert analyzed["error_message"] is None
        jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
        assert [j["status"] for j in jobs] == ["completed", "failed", "completed"]


def test_delete_project_removes_analysis_artifacts(client, deterministic_ai) -> None:
    test_client, settings = client
    prepared = _upload_prepared(test_client)
    project_id = prepared["id"]
    _analyze_to_analyzed(test_client, project_id)
    project_dir = settings.projects_dir / project_id
    assert (project_dir / "analysis" / "metadata" / "timeline.json").is_file()

    assert test_client.delete(f"/api/projects/{project_id}").status_code == 204
    assert not project_dir.exists()
    assert test_client.get(f"/api/projects/{project_id}").status_code == 404


def test_analysis_endpoints_404_before_analysis(client, deterministic_ai) -> None:
    test_client, _ = client
    prepared = _upload_prepared(test_client)
    project_id = prepared["id"]
    assert test_client.get(f"/api/projects/{project_id}/analysis").status_code == 404
    assert test_client.get(f"/api/projects/{project_id}/timeline").status_code == 404
    assert test_client.get(f"/api/projects/{project_id}/analysis/frames/1").status_code == 404


# ----------------------------------------------------------------------
# Worker: strictly one analysis job at a time
# ----------------------------------------------------------------------

def test_analysis_jobs_run_serially(settings, tmp_path, monkeypatch, deterministic_ai) -> None:
    with _client_with_media(settings, tmp_path) as test_client:
        first = _upload_prepared(test_client)
        second = _upload_prepared(test_client, payload=VIDEO_BYTES + b"\x01")

        import app.services.worker as worker_module

        class SlowAnalysis:
            def __init__(self, settings_, storage_) -> None:
                pass

            def run(self, project_row, ffmpeg_path, progress_callback=None) -> dict:
                time.sleep(0.4)
                return {
                    "scene_count": 1,
                    "transcript_available": False,
                    "detected_language": None,
                    "language_probability": None,
                    "ocr_available": False,
                    "visual_provider": "deterministic",
                    "processing_seconds": 0.4,
                    "warnings": [],
                }

        monkeypatch.setattr(worker_module, "AnalysisService", SlowAnalysis)

        assert test_client.post(f"/api/projects/{first['id']}/analyze").status_code == 200
        assert test_client.post(f"/api/projects/{second['id']}/analyze").status_code == 200

        observed_running = 0
        deadline = time.time() + 15
        while time.time() < deadline:
            jobs = test_client.get(f"/api/projects/{first['id']}/jobs").json()
            jobs += test_client.get(f"/api/projects/{second['id']}/jobs").json()
            running = [j for j in jobs if j["status"] == "running"]
            assert len(running) <= 1, f"two analysis jobs running at once: {jobs}"
            observed_running += len(running)
            first_body = test_client.get(f"/api/projects/{first['id']}").json()
            second_body = test_client.get(f"/api/projects/{second['id']}").json()
            if first_body["status"] == "analyzed" and second_body["status"] == "analyzed":
                break
            time.sleep(0.05)

        assert first_body["status"] == "analyzed", first_body
        assert second_body["status"] == "analyzed", second_body
        assert observed_running > 0


# ----------------------------------------------------------------------
# System status: honest capability report
# ----------------------------------------------------------------------

def test_system_status_reports_analysis_capabilities(client) -> None:
    test_client, _ = client
    body = test_client.get("/api/system/status").json()
    assert body["phase"] == "5"
    analysis = body["analysis"]
    assert analysis["scene_detection"]["engine"] == "ffmpeg-select"
    assert analysis["scene_detection"]["available"] is True  # fake ffmpeg detected
    stt = analysis["speech_to_text"]
    assert stt["model_name"] == "tiny"
    assert stt["device"] == "cpu"
    assert stt["compute_type"] == "int8"
    assert stt["package"] in ("installed", "not_installed")
    assert stt["model"] in ("ready", "not_installed")
    assert analysis["ocr"]["available"] in (True, False)
    assert analysis["visual"]["provider"] == "deterministic"
    assert analysis["settings"]["scene_threshold"] == 0.3
    assert analysis["settings"]["max_scenes"] == 500
    assert analysis["settings"]["ocr_enabled"] is True


# ----------------------------------------------------------------------
# Scene assembly rules (deterministic unit tests)
# ----------------------------------------------------------------------

def test_scene_assembly_enforces_minimum_duration() -> None:
    from app.video.scenes import _assemble_scenes

    scenes = _assemble_scenes([1.0, 1.5, 2.2, 5.0], 10.0, 2.0, 500)
    assert all(s["duration"] >= 2.0 - 1e-6 for s in scenes)
    assert scenes[0]["start"] == 0.0
    assert scenes[-1]["end"] == 10.0
    assert [s["scene_id"] for s in scenes] == list(range(1, len(scenes) + 1))
    for scene in scenes:
        assert scene["end"] > scene["start"]
        assert scene["duration"] > 0
        assert 0 <= scene["start"] < scene["end"] <= 10.0


def test_scene_assembly_caps_maximum_count() -> None:
    from app.video.scenes import _assemble_scenes

    scenes = _assemble_scenes([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], 9.0, 0.5, 3)
    assert len(scenes) <= 3
    assert scenes[-1]["end"] == 9.0


def test_scene_assembly_single_scene_without_changes() -> None:
    from app.video.scenes import _assemble_scenes

    scenes = _assemble_scenes([], 12.5, 2.0, 500)
    assert len(scenes) == 1
    assert scenes[0]["start"] == 0.0
    assert scenes[0]["end"] == 12.5
    assert scenes[0]["representative_timestamp"] == 6.25


# ----------------------------------------------------------------------
# Speech-to-text service (fake engine replaces the heavy Whisper model)
# ----------------------------------------------------------------------

class _FakeSegment:
    def __init__(self, segment_id, start, end, text, avg_logprob) -> None:
        self.id = segment_id
        self.start = start
        self.end = end
        self.text = text
        self.avg_logprob = avg_logprob


class _FakeInfo:
    language = "en"
    language_probability = 0.95


class _FakeWhisperEngine:
    def __init__(self) -> None:
        self.language_arg = "unset"

    def transcribe(self, path, language=None, beam_size=5, vad_filter=True):
        self.language_arg = language
        segments = [
            _FakeSegment(0, 0.0, 1.5, "Hello world", -0.1),
            _FakeSegment(1, 1.5, 3.0, "Second sentence", -0.05),
        ]
        return iter(segments), _FakeInfo()


def _stt_service(settings):
    from app.ai.stt import SpeechToTextService

    service = SpeechToTextService(settings)
    service._model = _FakeWhisperEngine()  # lazy-load returns this engine
    return service


def test_stt_transcript_structure_timestamps_and_progress(settings) -> None:
    service = _stt_service(settings)
    progress: list[float] = []
    doc = service.transcribe(
        "audio.wav",
        preferred_language="en",
        duration_seconds=3.0,
        progress_callback=progress.append,
    )

    assert doc["schema_version"] == 1
    assert doc["language"] == "en"          # auto-detected (preferred mode)
    assert doc["language_probability"] == 0.95
    assert doc["duration_seconds"] == 3.0
    assert len(doc["segments"]) == 2
    first = doc["segments"][0]
    assert first["id"] == 0
    assert first["start"] == 0.0 and first["end"] == 1.5
    assert first["text"] == "Hello world"
    assert first["confidence"] == -0.1
    assert doc["segments"][1]["start"] == 1.5 and doc["segments"][1]["end"] == 3.0
    # Segment-end progress, never invented.
    assert progress == [0.5, 1.0]

    service.release_model()
    assert service._model is None


def test_stt_forced_language_mode(settings) -> None:
    settings.whisper_language_mode = "forced"
    service = _stt_service(settings)
    doc = service.transcribe(
        "audio.wav", preferred_language="hi", duration_seconds=3.0
    )
    assert service._model.language_arg == "hi"
    assert doc["language"] == "hi"
    assert doc["language_probability"] is None


def test_stt_missing_model_reports_download_required(settings, tmp_path) -> None:
    from app.ai.stt import SpeechToTextService
    from app.utils.errors import WhisperModelMissingError

    service = SpeechToTextService(settings)
    service._model = None  # force the availability gate
    with pytest.raises(WhisperModelMissingError) as excinfo:
        service.transcribe("audio.wav", preferred_language="en", duration_seconds=3.0)
    assert "model" in str(excinfo.value).lower()
    assert "download" in str(excinfo.value).lower()


# ----------------------------------------------------------------------
# OCR service (pytesseract faked; binary absence handled honestly)
# ----------------------------------------------------------------------

def _make_jpeg(path, color=(200, 100, 50)) -> str:
    from PIL import Image

    Image.new("RGB", (64, 48), color).save(path, "JPEG")
    return str(path)


def test_ocr_extracts_and_deduplicates_frames(tmp_path, monkeypatch, settings) -> None:
    from app.ai.ocr import OCRService

    monkeypatch.setattr("app.ai.ocr.tesseract_available", lambda s: True)
    monkeypatch.setattr("app.ai.ocr._tesseract_binary", lambda s: "/usr/bin/tesseract")

    import pytesseract

    calls = {"count": 0}

    def fake_image_to_data(image, output_type=None) -> dict:
        calls["count"] += 1
        return {"text": ["", "HELLO", "WORLD", ""], "conf": ["-1", "92", "88", "-1"]}

    monkeypatch.setattr(pytesseract, "image_to_data", fake_image_to_data)

    results = OCRService(settings).run_on_frames([
        {"timestamp": 1.0, "path": _make_jpeg(tmp_path / "a.jpg")},
        {"timestamp": 2.0, "path": _make_jpeg(tmp_path / "b.jpg")},
    ])
    # Both frames were OCR'd but the duplicate line is dropped.
    assert calls["count"] == 2
    assert len(results) == 1
    assert results[0]["timestamp"] == 1.0
    assert results[0]["text"] == "HELLO WORLD"
    assert results[0]["confidence"] == 90.0


def test_ocr_unavailable_raises_clear_error(tmp_path, monkeypatch, settings) -> None:
    from app.ai.ocr import OCRService
    from app.utils.errors import TesseractUnavailableError

    monkeypatch.setattr("app.ai.ocr.tesseract_available", lambda s: False)
    with pytest.raises(TesseractUnavailableError):
        OCRService(settings).run_on_frames([{"timestamp": 1.0, "path": "x.jpg"}])


def test_ocr_missing_frame_is_skipped(tmp_path, monkeypatch, settings) -> None:
    from app.ai.ocr import OCRService

    monkeypatch.setattr("app.ai.ocr.tesseract_available", lambda s: True)
    monkeypatch.setattr("app.ai.ocr._tesseract_binary", lambda s: "/usr/bin/tesseract")
    import pytesseract

    monkeypatch.setattr(
        pytesseract, "image_to_data",
        lambda image, output_type=None: {"text": ["VISIBLE"], "conf": ["99"]},
    )
    results = OCRService(settings).run_on_frames([
        {"timestamp": 1.0, "path": str(tmp_path / "missing.jpg")},
        {"timestamp": 2.0, "path": _make_jpeg(tmp_path / "real.jpg")},
    ])
    assert len(results) == 1
    assert results[0]["text"] == "VISIBLE"


# ----------------------------------------------------------------------
# Vision analysis (deterministic PIL metadata, no hallucinated claims)
# ----------------------------------------------------------------------

def test_vision_deterministic_metadata(tmp_path, settings) -> None:
    from app.ai.vision import VisionAnalysisService

    service = VisionAnalysisService(settings)
    result = service.analyze_frames([
        {"timestamp": 3.0, "path": _make_jpeg(tmp_path / "frame.jpg"),
         "rel_path": "frames/scene_000.jpg"},
    ])
    assert result["provider"] == "deterministic"
    frame = result["frames"][0]
    assert frame["timestamp"] == 3.0
    assert frame["frame_path"] == "frames/scene_000.jpg"
    assert frame["width"] == 64 and frame["height"] == 48
    assert 0 <= frame["brightness"] <= 255
    assert 0 <= frame["blur_estimate"] <= 1
    assert 0 <= frame["complexity"] <= 1
    assert isinstance(frame["edge_energy"], float)


def test_vision_skips_unreadable_frames(tmp_path, settings) -> None:
    from app.ai.vision import VisionAnalysisService

    result = VisionAnalysisService(settings).analyze_frames([
        {"timestamp": 1.0, "path": str(tmp_path / "nope.jpg"), "rel_path": "x.jpg"},
    ])
    assert result["frames"] == []


# ----------------------------------------------------------------------
# Timeline alignment & information density
# ----------------------------------------------------------------------

def _timeline_inputs():
    scenes = [
        {"scene_id": 1, "start": 0.0, "end": 10.0, "duration": 10.0,
         "representative_timestamp": 5.0, "representative_frame": "frames/scene_000.jpg"},
        {"scene_id": 2, "start": 10.0, "end": 20.0, "duration": 10.0,
         "representative_timestamp": 15.0, "representative_frame": "frames/scene_001.jpg"},
    ]
    transcript = {
        "schema_version": 1, "language": "en",
        "segments": [
            {"id": 0, "start": 2.0, "end": 4.0, "text": "hello there", "confidence": 0.9},
            {"id": 1, "start": 15.0, "end": 16.0, "text": "next part", "confidence": 0.8},
        ],
    }
    ocr = [
        {"timestamp": 3.0, "text": "TITLE", "confidence": 0.7},
        {"timestamp": 15.5, "text": "SIGN", "confidence": 0.6},
    ]
    visual = {
        "provider": "deterministic",
        "frames": [
            {"timestamp": 5.0, "frame_path": "frames/scene_000.jpg",
             "blur_estimate": 0.2, "complexity": 0.5, "brightness": 100.0,
             "width": 64, "height": 48},
            {"timestamp": 15.0, "frame_path": "frames/scene_001.jpg",
             "blur_estimate": 0.8, "complexity": 0.1, "brightness": 200.0,
             "width": 64, "height": 48},
        ],
    }
    return scenes, transcript, ocr, visual


def test_timeline_matches_transcript_and_ocr_to_scenes() -> None:
    from app.services.timeline import build_timeline

    scenes, transcript, ocr, visual = _timeline_inputs()
    doc = build_timeline(
        scenes=scenes, transcript=transcript, ocr_results=ocr,
        visual_results=visual, duration_seconds=20.0,
    )
    assert doc["schema_version"] == 1
    assert doc["summary"]["scene_count"] == 2
    assert doc["summary"]["speech_scenes"] == 2
    assert doc["summary"]["ocr_scenes"] == 2
    assert doc["summary"]["total_words"] == 4

    scene1, scene2 = doc["scenes"]
    assert [s["id"] for s in scene1["speech"]] == [0]
    assert [e["text"] for e in scene1["ocr"]] == ["TITLE"]
    assert [s["id"] for s in scene2["speech"]] == [1]
    assert [e["text"] for e in scene2["ocr"]] == ["SIGN"]
    assert scene1["visual"]["timestamp"] == 5.0
    assert scene2["visual"]["timestamp"] == 15.0
    assert scene1["representative_frame"] == "frames/scene_000.jpg"


def test_information_density_ranks_evidence() -> None:
    from app.services.timeline import build_timeline

    scenes, transcript, ocr, visual = _timeline_inputs()
    doc = build_timeline(
        scenes=scenes, transcript=transcript, ocr_results=ocr,
        visual_results=visual, duration_seconds=20.0,
    )
    d1 = doc["scenes"][0]["information_density"]  # speech + words + OCR + sharp
    d2 = doc["scenes"][1]["information_density"]  # less speech, blurry frame
    assert 0 <= d1 <= 100 and 0 <= d2 <= 100
    assert d1 > d2


def test_quality_check_flags_invalid_timestamps() -> None:
    from app.services.timeline import build_timeline, quality_check

    scenes, transcript, ocr, visual = _timeline_inputs()
    transcript["segments"][1]["end"] = 25.0  # beyond the video
    ocr.append({"timestamp": 99.0, "text": "OUTSIDE", "confidence": 0.5})
    doc = build_timeline(
        scenes=scenes, transcript=transcript, ocr_results=ocr,
        visual_results=visual, duration_seconds=20.0,
    )
    warnings = quality_check(
        scenes=scenes, transcript=transcript, ocr_results=ocr,
        timeline=doc, duration_seconds=20.0,
    )
    assert any("exceeds" in w.lower() for w in warnings)
    assert any("outside" in w.lower() for w in warnings)


# ----------------------------------------------------------------------
# Fingerprints (idempotency foundation)
# ----------------------------------------------------------------------

def test_config_fingerprint_changes_with_settings(settings) -> None:
    from app.utils.fingerprints import analysis_config_fingerprint

    before = analysis_config_fingerprint(settings)
    settings.scene_threshold = 0.9
    changed = analysis_config_fingerprint(settings)
    settings.min_scene_duration_seconds = 5.0
    assert analysis_config_fingerprint(settings) != changed
    assert analysis_config_fingerprint(settings) == analysis_config_fingerprint(settings)
    assert before != changed


def test_preprocessing_fingerprint_tracks_consumed_assets() -> None:
    from app.utils.fingerprints import preprocessing_fingerprint

    row = {"sha256": "abc", "duration": 12.5, "analysis_width": 640,
           "analysis_height": 360, "analysis_fps": 5.0}
    assert preprocessing_fingerprint(row) == preprocessing_fingerprint(dict(row))
    row["analysis_fps"] = 10.0
    assert preprocessing_fingerprint(row) != preprocessing_fingerprint(
        {"sha256": "abc", "duration": 12.5, "analysis_width": 640,
         "analysis_height": 360, "analysis_fps": 5.0}
    )