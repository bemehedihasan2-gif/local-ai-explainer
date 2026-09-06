"""Filesystem path safety helpers.

Used everywhere later phases will touch user-supplied filenames or input
paths. Paths are validated against an allowed base directory (resolved and
compared lexically after ``resolve()``) so ``../`` traversal is impossible.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.utils.errors import PathTraversalError

# Characters that are dangerous or invalid on common filesystems.
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_INVALID_NAMES = {"", ".", "..", "con", "prn", "aux", "nul",
                  "com1", "com2", "com3", "com4", "com5",
                  "com6", "com7", "com8", "com9",
                  "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
                  "lpt6", "lpt7", "lpt8", "lpt9"}


def sanitize_filename(name: str | None, *, fallback: str = "untitled") -> str:
    """Return a safe bare filename for storage, or ``fallback``.

    Strips any directory components (both separators), removes characters
    that are invalid on Windows/Unix, collapses whitespace and caps length.
    """
    if not name:
        return fallback
    name = name.replace("\\", "/").split("/")[-1].strip()
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    if name.lower() in _INVALID_NAMES:
        return fallback
    return name[:180] or fallback


def safe_join(base: Path, *parts: str) -> Path:
    """Join ``parts`` under ``base`` and refuse any path escaping ``base``."""
    base_resolved = base.expanduser().resolve()
    candidate = base_resolved.joinpath(*parts)
    try:
        candidate_resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover - rare OS edge case
        raise PathTraversalError("Could not resolve the requested path.") from exc
    if candidate_resolved != base_resolved and base_resolved not in candidate_resolved.parents:
        raise PathTraversalError(
            f"Path '{'/'.join(parts)}' escapes the allowed directory."
        )
    return candidate_resolved


def is_within(base: Path, candidate: Path) -> bool:
    """True when ``candidate`` is inside ``base`` (both resolved)."""
    try:
        base_resolved = base.expanduser().resolve()
        candidate_resolved = candidate.expanduser().resolve()
    except OSError:
        return False
    return candidate_resolved == base_resolved or base_resolved in candidate_resolved.parents
