"""SQLite access.

Design notes for the 8 GB target machine:

- SQLite is embedded, has no server process and keeps idle RAM near zero.
- A fresh short-lived connection is opened per operation (cheap for SQLite,
  safe across FastAPI's thread pool, no shared mutable state).
- ``WAL`` journal mode is enabled per connection so a future background job
  writer can run concurrently with API readers.
- Foreign keys are enforced per connection (``PRAGMA foreign_keys=ON``).
- No ORM: plain SQL keeps memory and startup overhead minimal.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.database.schema import SCHEMA_SQL
from app.utils.errors import DatabaseOperationError
from app.utils.logging import get_logger

logger = get_logger("app.database.connection")


class Database:
    """Thin wrapper around a single SQLite database file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.initialized = False

    # -- lifecycle -------------------------------------------------------
    def initialize(self) -> "Database":
        """Create parent directory + schema (idempotent)."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(SCHEMA_SQL)
        except (sqlite3.Error, OSError) as exc:
            raise DatabaseOperationError(
                f"Could not initialize database at '{self.path}': {exc}"
            ) from exc
        self.initialized = True
        logger.info("SQLite database ready at %s", self.path)
        return self

    # -- connections -----------------------------------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            if conn is not None:
                conn.rollback()
            raise DatabaseOperationError(str(exc)) from exc
        finally:
            if conn is not None:
                conn.close()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection for one repository operation."""
        with self._connect() as conn:
            yield conn

    @property
    def exists(self) -> bool:
        return self.path.exists()


__all__ = ["Database"]
