"""Checks 5-6 (+list/delete): project lifecycle through the API."""

from __future__ import annotations


def _make_project(client, **overrides) -> dict:
    payload = {
        "original_filename": "gameplay_clip.mp4",
        "language": "en",
        "target_duration_minutes": 3,
        **overrides,
    }
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_project_returns_record(client) -> None:
    body = _make_project(client, language="hi", target_duration_minutes=4)
    assert body["id"]
    assert body["original_filename"] == "gameplay_clip.mp4"
    assert body["language"] == "hi"
    assert body["target_duration_seconds"] == 240  # 4 minutes stored in seconds
    assert body["status"] == "created"
    assert body["progress"] == 0
    assert body["created_at"]
    assert body["updated_at"]
    assert body["error_message"] is None


def test_create_project_without_filename_gets_safe_fallback(client) -> None:
    body = _make_project(client, original_filename=None)
    assert body["original_filename"] == "untitled-video"
    # Windows-path traversal attempt is neutralized to a bare filename.
    evil = _make_project(client, original_filename="..\\..\\..\\evil.mp4")
    assert evil["original_filename"] == "evil.mp4"
    assert "/" not in evil["original_filename"] and "\\" not in evil["original_filename"]


def test_get_project_round_trip(client) -> None:
    created = _make_project(client)
    response = client.get(f"/api/projects/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_list_projects_newest_first(client) -> None:
    first = _make_project(client, original_filename="a.mp4")
    second = _make_project(client, original_filename="b.mp4")
    response = client.get("/api/projects")
    assert response.status_code == 200
    items = response.json()
    assert [p["id"] for p in items[:2]] == [second["id"], first["id"]]


def test_delete_project_and_verify_gone(client) -> None:
    created = _make_project(client)
    response = client.delete(f"/api/projects/{created['id']}")
    assert response.status_code == 204
    assert client.get(f"/api/projects/{created['id']}").status_code == 404
    # Deleting an unknown project is a clean 404 too.
    assert client.delete("/api/projects/does-not-exist").status_code == 404


def test_invalid_create_payload_is_rejected(client) -> None:
    response = client.post(
        "/api/projects",
        json={"language": "en", "target_duration_minutes": 9},
    )
    assert response.status_code == 422

    response = client.post("/api/projects", json={"language": "fr"})
    assert response.status_code == 422
