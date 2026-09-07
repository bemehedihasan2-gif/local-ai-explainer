"""Checks 4: SQLite database initializes with the expected schema."""

from __future__ import annotations

import sqlite3

from app.database.connection import Database
from app.database.repositories.jobs import JobRepository
from app.database.repositories.projects import ProjectRepository


def test_database_initializes_schema(settings) -> None:
    db = Database(settings.database_path).initialize()
    assert db.path.exists()
    assert db.initialized

    with db.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"projects", "processing_jobs", "analysis_results", "script_runs"} <= tables

    # Phase 5 script_runs columns (additive schema, backward compatible).
    with db.connect() as conn:
        script_columns = [
            row[1] for row in conn.execute("PRAGMA table_info(script_runs)").fetchall()
        ]
    for expected in (
        "id", "project_id", "status", "current_stage", "language",
        "target_duration_seconds", "content_type", "selected_scene_count",
        "word_count", "quality_score", "generation_fingerprint",
        "error_message", "warnings", "created_at", "updated_at",
    ):
        assert expected in script_columns, f"missing script_runs column: {expected}"

    # Idempotent: a second initialize must not raise or duplicate anything.
    Database(settings.database_path).initialize()
    with db.connect() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()]
    for expected in (
        "id", "original_filename", "input_path", "duration", "width", "height",
        "fps", "language", "target_duration_seconds", "status", "progress",
        "error_message", "created_at", "updated_at",
    ):
        assert expected in columns, f"missing projects column: {expected}"


def test_foreign_key_cascade_removes_jobs(settings) -> None:
    db = Database(settings.database_path).initialize()
    projects = ProjectRepository(db)
    jobs = JobRepository(db)

    project = projects.create(
        original_filename="demo.mp4",
        stored_filename=None,
        language="en",
        target_duration_seconds=120,
    )
    job = jobs.create(project_id=project["id"], stage="scene_detection")
    assert jobs.get(job["id"])["project_id"] == project["id"]

    projects.delete(project["id"])

    # Cascade delete removed the job together with the project.
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM processing_jobs WHERE project_id = ?",
            (project["id"],),
        ).fetchone()
    assert rows[0] == 0


def test_database_errors_are_wrapped(settings, tmp_path) -> None:
    # A path pointing *into a file* cannot hold a database -> wrapped error.
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    from app.utils.errors import ExplainerError

    db = Database(blocker / "sub" / "db.sqlite")
    try:
        db.initialize()
    except ExplainerError as exc:
        assert "database" in exc.message.lower()
    else:
        raise AssertionError("expected an ExplainerError for an invalid db path")


def test_invalid_project_id_returns_proper_error(client) -> None:
    """Check 7: unknown/malformed ids return a clean 404 JSON error."""
    for bad_id in ("nope", "not-a-real-uuid-here", "0" * 40):
        response = client.get(f"/api/projects/{bad_id}")
        assert response.status_code == 404
        body = response.json()
        assert body["error"] == "project_not_found"
        assert body["detail"]


def test_sqlite_core_available(settings) -> None:
    """sqlite3 stdlib import and version sanity (used by system status)."""
    import sqlite3

    assert sqlite3.sqlite_version_info >= (3,)
    conn = sqlite3.connect(":memory:")
    assert conn.execute("SELECT 1").fetchone()[0] == 1
    conn.close()
