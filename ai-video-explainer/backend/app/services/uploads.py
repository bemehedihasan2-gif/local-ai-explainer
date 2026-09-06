"""Streaming upload & validation engine (Phase 2).

Flow per upload:

    create project row (UPLOADING, 0%)
    -> stream chunks to projects/<id>/input/<stored>.<ext>
       (never more than ``upload_chunk_size`` bytes in memory,
        cumulative size enforced, SHA-256 computed while streaming)
    -> VALIDATING (90%)
    -> FFprobe metadata extraction + validation
    -> READY (100%) with metadata stored   |   FAILED (+ error) on any failure

Failure handling: the project row is marked ``FAILED`` with a readable
message, the partially written file and project folder are removed, and the
original exception is re-raised for the API layer to map to a clean error.
Duplicate detection (same SHA-256, existing non-failed project) returns 409
and removes the throwaway upload rather than cluttering project history.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from app.database.connection import Database
from app.database.repositories.projects import ProjectRepository
from app.models.enums import ProjectStatus
from app.services.ffmpeg import FfmpegService
from app.services.storage import StorageService
from app.utils.errors import (
    DuplicateVideoError,
    ExplainerError,
    InvalidVideoError,
    MissingFileError,
    ProjectNotFoundError,
    StorageError,
    UnsupportedFileTypeError,
    UploadTooLargeError,
)
from app.utils.logging import get_logger
from app.utils.paths import sanitize_filename
from app.video.probe import probe_media

logger = get_logger("app.services.uploads")

#: Progress milestones that never claim a precision we do not have.
_UPLOAD_MAX_PCT = 90.0  # uploading maps bytes -> 0..90; validating holds 90.


class UploadService:
    """Owns the safe receive -> store -> validate -> READY pipeline."""

    def __init__(
        self,
        settings: Any,
        db: Database,
        ffmpeg: FfmpegService,
        storage: StorageService,
    ) -> None:
        self._settings = settings
        self._db = db
        self._ffmpeg = ffmpeg
        self._storage = storage
        self._repo = ProjectRepository(db)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def handle_upload(
        self,
        *,
        upload_file: Any,
        original_filename: str | None,
        language: str,
        target_duration_seconds: int,
        progress_callback: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        """Validate, store and probe an uploaded video.

        Returns the full project row (internal fields included).
        Raises an :class:`ExplainerError` subclass on every failure.
        """
        # Fast fail before any project row / file is created.
        ffprobe = self._ffmpeg.require().ffprobe_path
        ext = self._validate_extension(original_filename)

        safe_original = sanitize_filename(original_filename, fallback="video")
        project = self._repo.create(
            original_filename=safe_original,
            language=language,
            target_duration_seconds=target_duration_seconds,
            status=ProjectStatus.UPLOADING,
            progress=0.0,
        )
        project_id = project["id"]
        self._report(progress_callback, 0.0)
        logger.info(
            "Upload started for project %s (%s, target=%ss)",
            project_id, safe_original, target_duration_seconds,
            extra={"project_id": project_id},
        )

        try:
            stored_name = f"{project_id[:16]}{ext}"
            dirs = self._storage.ensure_project_dirs(project_id)
            dest = dirs["input"] / stored_name
            rel_input = f"input/{stored_name}"

            file_size = self._stream_to_disk(
                upload_file, dest, project_id, progress_callback
            )

            sha256 = self._compute_sha256(dest)
            self._repo.update(project_id, sha256=sha256, file_size=file_size)
            logger.info(
                "Upload stored for project %s (%d bytes, sha256=%s…)",
                project_id, file_size, sha256[:12], extra={"project_id": project_id},
            )

            self._reject_if_duplicate(project_id, sha256, safe_original)

            # ---- FFprobe validation -----------------------------------
            self._repo.update(project_id, status=ProjectStatus.VALIDATING)
            metadata = probe_media(
                str(ffprobe),
                dest,
                timeout_seconds=self._settings.ffprobe_timeout_seconds,
            )
            # The bytes actually stored are authoritative for file_size.
            metadata["file_size"] = dest.stat().st_size
            metadata["stored_filename"] = rel_input
            metadata["input_path"] = str(dest)
            self._repo.update(
                project_id,
                status=ProjectStatus.READY,
                progress=100.0,
                **metadata,
            )
            self._report(progress_callback, 100.0)
            logger.info(
                "Project %s is READY (video validated).",
                project_id, extra={"project_id": project_id},
            )
            return self._repo.get(project_id)

        except ExplainerError as exc:
            self._fail(project_id, exc.message, remove_files=True)
            raise
        except OSError as exc:  # e.g. disk full / permission denied mid-write
            message = f"Could not store the upload on disk: {exc}"
            self._fail(project_id, message, remove_files=True)
            raise StorageError(message) from exc

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------
    def _validate_extension(self, original_filename: str | None) -> str:
        if not original_filename:
            raise MissingFileError("No file was provided with the upload request.")
        ext = Path(original_filename).suffix.lower()
        if ext not in self._settings.allowed_video_extensions:
            allowed = ", ".join(self._settings.allowed_video_extensions)
            raise UnsupportedFileTypeError(
                f"'{original_filename}' has an unsupported file type. "
                f"Supported video formats: {allowed}."
            )
        return ext

    def _stream_to_disk(
        self,
        upload_file: Any,
        dest: Path,
        project_id: str,
        progress_callback: Callable[[float], None] | None,
    ) -> int:
        """Copy chunks to ``dest``; returns total bytes written.

        Reads the source (an already spooled multipart part) in bounded
        chunks, enforces the cumulative size limit and never truncates:
        exceeding the limit aborts before any further bytes are written.
        """
        max_bytes = self._settings.max_upload_size_bytes
        chunk_size = self._settings.upload_chunk_size
        total = getattr(upload_file, "size", None)  # content-length, if known
        total = int(total) if isinstance(total, int) and total > 0 else None

        written = 0
        last_persisted = -1.0
        digest = hashlib.sha256()
        try:
            with dest.open("wb") as out:
                while True:
                    chunk = upload_file.file.read(chunk_size)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise UploadTooLargeError(
                            "Video file exceeds the maximum allowed upload size "
                            f"of {self._settings.max_upload_size_mb} MB."
                        )
                    out.write(chunk)
                    digest.update(chunk)
                    if total:
                        pct = round(written / total * _UPLOAD_MAX_PCT, 1)
                        if pct - last_persisted >= 1.0:
                            self._repo.update(project_id, progress=pct)
                            self._report(progress_callback, pct)
                            last_persisted = pct
        except (OSError, ExplainerError):
            # Abort the write immediately; caller handles cleanup + errors.
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        if written == 0:
            raise InvalidVideoError(
                "The uploaded file is empty (0 bytes) and cannot be a video."
            )
        return written

    @staticmethod
    def _compute_sha256(path: Path) -> str:
        """Streaming SHA-256: bounded memory regardless of file size."""
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _reject_if_duplicate(
        self, project_id: str, sha256: str, filename: str
    ) -> None:
        """409 when the identical file already exists as a non-failed project.

        The freshly uploaded copy is removed; the *existing* project is left
        untouched so the user can inspect or delete it themselves.
        """
        duplicates = [
            p for p in self._repo.find_by_sha256(sha256)
            if p["id"] != project_id and p["status"] != ProjectStatus.FAILED.value
        ]
        if not duplicates:
            return
        existing = duplicates[0]
        logger.info(
            "Duplicate upload detected for project %s (matches %s)",
            project_id, existing["id"],
            extra={"project_id": project_id},
        )
        try:
            self._repo.delete(project_id)
            self._storage.remove_project_directory(project_id)
        except (ProjectNotFoundError, ExplainerError, OSError) as exc:  # pragma: no cover
            logger.warning("Duplicate cleanup was incomplete: %s", exc)
        raise DuplicateVideoError(
            f"'{filename}' is a duplicate of an existing video "
            f"('{existing['original_filename']}', project {existing['id']}). "
            "Delete the earlier project if you want to upload it again.",
            project_id=project_id,
            existing_project_id=existing["id"],
            existing_filename=existing["original_filename"],
        )

    def _fail(self, project_id: str, message: str, *, remove_files: bool) -> None:
        """Mark the project FAILED, record the error, remove partial files."""
        try:
            self._repo.update(
                project_id, status=ProjectStatus.FAILED, error_message=message
            )
        except ProjectNotFoundError:  # pragma: no cover - row always exists here
            pass
        logger.error(
            "Project %s FAILED: %s", project_id, message,
            extra={"project_id": project_id},
        )
        if remove_files:
            try:
                self._storage.remove_project_directory(project_id)
            except ExplainerError as exc:
                logger.warning(
                    "Could not clean up project folder %s: %s", project_id, exc,
                    extra={"project_id": project_id},
                )

    @staticmethod
    def _report(
        callback: Callable[[float], None] | None, progress: float
    ) -> None:
        if callback is not None:
            callback(progress)


__all__ = ["UploadService"]
