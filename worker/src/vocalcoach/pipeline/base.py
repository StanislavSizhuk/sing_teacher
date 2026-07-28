"""Stage contract every pipeline stage implements (spec 6.1, 12.3).

Adding a stage means adding a class here and listing it in the runner's
stage order -- `PipelineRunner` itself never changes (Open/Closed, spec
12.3: "adding a new stage must not require editing the runner").
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult


class PipelineStage(ABC):
    """One step of the ML pipeline (spec 6.2 table)."""

    #: Stable identifier stored in `analyses.stages_json` and `current_stage`.
    name: str
    #: Wall-clock budget for `run` (spec 6.2), enforced by `PipelineRunner`
    #: from outside this class -- a stage never times itself.
    timeout_seconds: int

    @abstractmethod
    def run(self, context: AnalysisContext) -> StageResult:
        """Executes this stage against `context` and returns its result.

        Must be idempotent and side-effect-free beyond `context` and the
        database (spec 6.1): running it twice on the same input produces
        the same output, so a retried job can safely re-run a stage that
        never got to persist its result.
        """
        raise NotImplementedError
