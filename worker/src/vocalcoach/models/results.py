"""Stage output contract (spec 6.1)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class StageStatus(StrEnum):
    DONE = "done"
    FAILED = "failed"
    #: An optional stage (`PipelineStage.required = False`, spec 6.3) that
    #: exhausted its retries or failed logically. The pipeline continues;
    #: `error_code`/`error_message` on this result carry why (FR-18).
    SKIPPED = "skipped"


class StageResult(BaseModel):
    """JSON-serializable output of one pipeline stage, written into
    `analyses.stages_json[stage]` (spec 6.1). `data` is aspect-specific --
    each stage module documents its own shape."""

    stage: str
    status: StageStatus
    duration_ms: int
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
