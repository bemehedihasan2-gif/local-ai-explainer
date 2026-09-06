"""Extra guardrails: honest AI stubs, path safety, cleanup foundations."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.ai.base import PipelineContext
from app.ai.registry import REGISTRY, instantiate
from app.services.cleanup import remove_stale_files
from app.utils.errors import NotInPhase1Error, PathTraversalError
from app.utils.paths import safe_join, sanitize_filename


def test_every_registered_service_refuses_to_fake_work(settings) -> None:
    """Phase 1 rule: stubs raise, they never return fake AI results."""
    assert REGISTRY  # non-empty registry
    ctx = PipelineContext(project_id="abc", settings=settings)
    for service in instantiate(settings):
        with pytest.raises(NotInPhase1Error) as exc_info:
            asyncio.run(service.execute(ctx))
        message = str(exc_info.value)
        assert "Phase 1" in message and service.name in message
        assert "No real processing was performed" in message


def test_sanitize_filename_strips_traversal() -> None:
    assert sanitize_filename("..\\..\\windows\\evil.exe") == "evil.exe"
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\\\temp\\\\clip.mp4") == "clip.mp4"
    assert sanitize_filename("") == "untitled"
    assert sanitize_filename(None) == "untitled"
    assert len(sanitize_filename("x" * 500 + ".mp4")) <= 180


def test_safe_join_rejects_escape(tmp_path) -> None:
    base = tmp_path / "uploads"
    base.mkdir()
    assert safe_join(base, "ok", "file.mp4") == (base / "ok" / "file.mp4").resolve()
    with pytest.raises(PathTraversalError):
        safe_join(base, "..", "secret.txt")
    with pytest.raises(PathTraversalError):
        safe_join(base, "sub", "..", "..", "escape.txt")


def test_cleanup_removes_only_stale_direct_children(tmp_path) -> None:
    folder = tmp_path / "temp"
    folder.mkdir()
    fresh = folder / "fresh.tmp"
    stale = folder / "stale.tmp"
    nested = folder / "sub"
    nested.mkdir()
    old_in_nested = nested / "old.tmp"
    fresh.write_text("x")
    stale.write_text("y")
    old_in_nested.write_text("z")

    # Make 'stale' older than the age limit.
    old_ts = time.time() - 7200
    os.utime(stale, (old_ts, old_ts))
    os.utime(old_in_nested, (old_ts, old_ts))

    removed = remove_stale_files(folder, max_age_seconds=3600)
    assert removed == 1  # only the stale top-level file
    assert fresh.exists()
    assert not stale.exists()
    assert old_in_nested.exists()  # never recurses into subdirectories
