"""SQLite schema.

Two tables: ``projects`` and ``processing_jobs``. ``CREATE TABLE IF NOT
EXISTS`` covers fresh installs; :func:`migrate_schema` adds columns that were
introduced after a database was first created (e.g. the Phase 2 media
metadata columns), so existing Phase 1 data is never destroyed.
"""

from __future__ import annotations

import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id                    TEXT PRIMARY KEY,
    original_filename     TEXT NOT NULL,
    stored_filename       TEXT,
    input_path            TEXT,
    file_size             INTEGER,
    sha256                TEXT,
    duration              REAL,
    width                 INTEGER,
    height                INTEGER,
    fps                   REAL,
    raw_fps               TEXT,
    video_codec           TEXT,
    audio_codec           TEXT,
    container_format      TEXT,
    bitrate               INTEGER,
    has_video             INTEGER NOT NULL DEFAULT 0,
    has_audio             INTEGER NOT NULL DEFAULT 0,
    language              TEXT NOT NULL DEFAULT 'en',
    target_duration_seconds INTEGER NOT NULL,
    status                TEXT NOT NULL DEFAULT 'created',
    progress              REAL NOT NULL DEFAULT 0,
    error_message         TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_status
    ON projects (status);
CREATE INDEX IF NOT EXISTS idx_projects_created_at
    ON projects (created_at DESC);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL
                  REFERENCES projects (id) ON DELETE CASCADE,
    stage         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',
    progress      REAL NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at    TEXT,
    completed_at  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_project_id
    ON processing_jobs (project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status
    ON processing_jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_project_status
    ON processing_jobs (project_id, status);
"""

#: Columns added after the initial Phase 1 release, applied by
#: :func:`migrate_schema` when they are missing from an existing table.
_PHASE2_COLUMNS: dict[str, str] = {
    "file_size": "INTEGER",
    "sha256": "TEXT",
    "raw_fps": "TEXT",
    "video_codec": "TEXT",
    "audio_codec": "TEXT",
    "container_format": "TEXT",
    "bitrate": "INTEGER",
    "has_video": "INTEGER NOT NULL DEFAULT 0",
    "has_audio": "INTEGER NOT NULL DEFAULT 0",
}

#: Phase 3 analysis-asset columns (relative paths, never absolute).
_PHASE3_COLUMNS: dict[str, str] = {
    "analysis_path": "TEXT",       # relative: "analysis/analysis.mp4"
    "analysis_width": "INTEGER",
    "analysis_height": "INTEGER",
    "analysis_fps": "REAL",
    "thumbnail_path": "TEXT",      # relative: "thumbnails/poster.jpg"
    "audio_path": "TEXT",          # relative: "audio/audio.wav" (or NULL)
    "prepared_at": "TEXT",
}


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Add any columns introduced after the table was first created.

    Idempotent and additive only: existing rows are preserved and columns are
    never dropped or renamed. Runs after ``executescript`` on every startup,
    and only then creates the SHA-256 index (the column may not exist yet on
    pre-Phase-2 databases).
    """
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()
    }
    for column, definition in _PHASE2_COLUMNS.items():
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {definition}")
    for column, definition in _PHASE3_COLUMNS.items():
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {definition}")
    # Columns exist on fresh databases and after the ALTERs above.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_sha256 ON projects (sha256)"
    )


__all__ = ["SCHEMA_SQL", "migrate_schema"]
