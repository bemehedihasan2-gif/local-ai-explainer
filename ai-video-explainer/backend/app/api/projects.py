"""Projects endpoints.

Phase 1 scope: create/list/get/delete *records*. Uploads, FFprobe metadata
and any processing arrive in Phase 2; POST only persists the project and
returns its id so the frontend flow is real end-to-end.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import get_database
from app.database.connection import Database
from app.database.repositories.projects import ProjectRepository
from app.models.project import ProjectCreate, ProjectOut
from app.utils.logging import log_context
from app.utils.paths import sanitize_filename

router = APIRouter()


def _project_out(row: dict) -> ProjectOut:
    return ProjectOut.model_validate(row)


@router.get("/api/projects", response_model=list[ProjectOut])
def list_projects(
    db: Database = Depends(get_database),
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProjectOut]:
    with log_context():
        rows = ProjectRepository(db).list(limit=limit, offset=offset)
        return [_project_out(row) for row in rows]


@router.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    db: Database = Depends(get_database),
) -> ProjectOut:
    with log_context(project_id=project_id):
        return _project_out(ProjectRepository(db).get(project_id))


@router.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectCreate,
    db: Database = Depends(get_database),
) -> ProjectOut:
    with log_context():
        repo = ProjectRepository(db)
        safe_name = sanitize_filename(payload.original_filename, fallback="untitled-video")
        row = repo.create(
            original_filename=safe_name,
            stored_filename=None,  # files land in Phase 2 (uploads)
            language=payload.language_enum.value,
            target_duration_seconds=payload.target_duration_seconds,
        )
        with log_context(project_id=row["id"]):
            return _project_out(row)


@router.delete("/api/projects/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    db: Database = Depends(get_database),
) -> Response:
    with log_context(project_id=project_id):
        ProjectRepository(db).delete(project_id)
        return Response(status_code=204)
