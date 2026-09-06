"""Central application error hierarchy.

Every failure that should reach an API client as a clean, understandable
message is raised as an ``ExplainerError`` subclass. FastAPI exception
handlers registered in :mod:`app.main` translate them into JSON responses
with the right HTTP status code. Unknown errors are logged (never returned
verbatim) and mapped to a generic 500 response.
"""

from __future__ import annotations


class ExplainerError(Exception):
    """Base class for all application errors surfaced to the API."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        project_id: str | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.project_id = project_id

    default_message = "An internal error occurred."

    def public_detail(self) -> str:
        return self.message


class ConfigError(ExplainerError):
    """Invalid or inconsistent application configuration."""

    status_code = 500
    code = "invalid_configuration"
    default_message = "Application configuration is invalid."


class DatabaseOperationError(ExplainerError):
    """A SQLite operation failed (raised after the underlying exception)."""

    status_code = 500
    code = "database_error"
    default_message = "A database error occurred."


class StorageError(ExplainerError):
    """A filesystem operation on managed storage directories failed."""

    status_code = 500
    code = "filesystem_error"
    default_message = "A filesystem error occurred."


class StorageUnavailableError(StorageError):
    """A required storage directory is missing or not writable."""

    status_code = 503
    code = "storage_unavailable"
    default_message = "Required storage directories are not available."


class FFmpegUnavailableError(ExplainerError):
    """FFmpeg/ffprobe binaries could not be located or executed."""

    status_code = 503
    code = "ffmpeg_unavailable"
    default_message = (
        "FFmpeg was not found. Install FFmpeg (https://ffmpeg.org) and make "
        "sure 'ffmpeg' and 'ffprobe' are on PATH, or set FFMPEG_PATH/"
        "FFPROBE_PATH in your .env file."
    )


class ProjectNotFoundError(ExplainerError):
    """Requested project does not exist (unknown or malformed id)."""

    status_code = 404
    code = "project_not_found"
    default_message = "Project not found."


class NotInPhase1Error(ExplainerError):
    """Feature is deliberately not implemented yet (Phase 1 scope guard).

    Pipeline services raise this instead of faking AI results.
    """

    status_code = 501
    code = "not_implemented_phase_1"
    default_message = "This capability is not implemented yet in Phase 1."


class PathTraversalError(ExplainerError):
    """A caller supplied a path escaping an allowed base directory."""

    status_code = 400
    code = "invalid_path"
    default_message = "Invalid path: escaping the allowed directory is not permitted."
