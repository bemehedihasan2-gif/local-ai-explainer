"""Shared base for pipeline services.

Contract for every stage of the future pipeline (see docs/architecture.md):

- One class per stage, discovered through :mod:`app.ai.registry`.
- ``execute`` receives a :class:`PipelineContext` describing which project
  and work directories to use, and returns a small stage result dict.
- Implementations must be CPU-friendly and streaming/file-based; they must
  never load whole videos (or several models) into memory on the 8 GB
  target machine.

Phase 1 implementations only raise ``NotInPhase1Error`` - deliberately no
placeholder output that could be mistaken for real results.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.enums import PipelineStage
from app.utils.errors import NotInPhase1Error


@dataclass
class PipelineContext:
    """Everything one pipeline stage needs to do its job."""

    project_id: str
    job_id: str | None = None
    #: Resolved settings object (paths, limits, ffmpeg config).
    settings: Any | None = None
    #: Named work directories (uploads/temp/outputs/...) for this project.
    work_dirs: dict[str, Path] = field(default_factory=dict)


class PipelineService(ABC):
    """Base class implemented by every pipeline stage."""

    #: Pipeline stage this service implements.
    stage: PipelineStage
    #: Short display name.
    name: str
    #: Which roadmap phase will provide the real implementation.
    planned_for: str = "a later phase"

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        """Run this stage for ``ctx``. Phase 1: always a controlled refusal."""
        raise NotInPhase1Error(
            f"{self.name} is planned for {self.planned_for} and is not "
            "implemented yet (Phase 1). No real processing was performed."
        )

    def describe(self) -> dict[str, str]:
        return {
            "stage": self.stage.value,
            "label": self.stage.label,
            "service": self.name,
            "planned_for": self.planned_for,
        }
