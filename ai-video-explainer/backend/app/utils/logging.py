"""Structured application logging.

Every log line carries:

    timestamp | level | module | message | project=.. | job=..

``project_id``/``job_id`` default to ``-`` and are populated automatically
while inside a :func:`log_context` block (e.g. while handling a request for
a specific project). Errors are additionally mirrored to ``errors.log``.
Secrets must never be passed to the logger.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator

_LOGGER_PREFIX = "explainer"
_context: ContextVar[dict[str, str]] = ContextVar("explainer_log_context", default={})

_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s "
    "| project=%(project_id)s | job=%(job_id)s"
)
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_config_guard = False


class ContextFilter(logging.Filter):
    """Inject current contextvars values into every record (default '-').

    The keys must always exist on the record or the formatter raises, so the
    filter guarantees defaults before formatting runs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _context.get()
        record.project_id = ctx.get("project_id", "-")
        record.job_id = ctx.get("job_id", "-")
        return True


def _make_handler(target: Path, level: int, max_bytes: int, backup_count: int) -> RotatingFileHandler:
    target.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        target, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATEFMT))
    # Handler-level filter: applied to records that propagate up from child
    # loggers (e.g. ``explainer.app.main``), where logger-level filters of
    # ancestors are never executed.
    handler.addFilter(ContextFilter())
    return handler


def _make_console_handler(level: int) -> logging.StreamHandler:
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATEFMT))
    handler.addFilter(ContextFilter())
    return handler


def setup_logging(
    *,
    level: str = "INFO",
    logs_dir: str | Path | None = None,
    log_max_bytes: int = 5 * 1024 * 1024,
    log_backup_count: int = 3,
) -> None:
    """Configure the ``explainer.*`` logger family (idempotent).

    Files are optional: if ``logs_dir`` is not writable we fall back to
    console-only logging instead of crashing the app.
    """
    global _config_guard
    root_logger = logging.getLogger(_LOGGER_PREFIX)
    if _config_guard:
        # Keep settings (file targets) in sync even if re-invoked in tests.
        root_logger.handlers.clear()
    root_logger.setLevel(level.upper())
    root_logger.propagate = False
    root_logger.addHandler(_make_console_handler(getattr(logging, level.upper(), logging.INFO)))

    if logs_dir is not None:
        logs_path = Path(logs_dir)
        try:
            root_logger.addHandler(
                _make_handler(logs_path / "app.log", logging.DEBUG, log_max_bytes, log_backup_count)
            )
            root_logger.addHandler(
                _make_handler(
                    logs_path / "errors.log", logging.ERROR, log_max_bytes, log_backup_count
                )
            )
        except OSError:
            root_logger.warning("Logs directory '%s' is not writable; console-only logging.", logs_path)
    _config_guard = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger under the app namespace, e.g. ``app.api.projects``."""
    if name.startswith(_LOGGER_PREFIX):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_PREFIX}.{name}")


@contextmanager
def log_context(**fields: str) -> Iterator[None]:
    """Temporarily attach fields (project_id/job_id) to emitted log records."""
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)
