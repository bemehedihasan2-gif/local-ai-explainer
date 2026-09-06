"""Shared fixtures. Every test gets fully isolated settings + directories
under a pytest tmp path, so no test ever writes into the repo's data/.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make the backend package importable regardless of the current directory.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        base_dir=tmp_path / "project-root",
        database_path=tmp_path / "project-root" / "data" / "explainer.db",
        logs_dir=tmp_path / "project-root" / "logs",
        environment="test",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    """TestClient with startup lifespan executed (db init + dirs)."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def project_payload() -> dict:
    return {"original_filename": "my_tutorial.mp4", "language": "en", "target_duration_minutes": 2}
