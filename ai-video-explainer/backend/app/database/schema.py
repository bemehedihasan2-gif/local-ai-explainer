"""SQLite schema (initial Phase 1 tables).

Deliberately small: one ``projects`` table plus one ``processing_jobs`` table.
Later phases add tables (e.g. transcripts, subtitles, scenes) without
touching these definitions thanks to ``CREATE TABLE IF NOT EXISTS``.
"""

from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id                    TEXT PRIMARY KEY,
    original_filename     TEXT NOT NULL,
    stored_filename       TEXT,
    input_path            TEXT,
    duration              REAL,
    width                 INTEGER,
    height                INTEGER,
    fps                   REAL,
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
