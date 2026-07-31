"""Stage contract every pipeline stage implements (spec 6.1, 12.3).

Adding a stage means adding a class here and listing it in the runner's
stage order -- `PipelineRunner` itself never changes (Open/Closed, spec
12.3: "adding a new stage must not require editing the runner").
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

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


@dataclass(frozen=True)
class ParallelGroup:
    """A batch of stages with no dependency on each other -- every aspect
    stage past alignment only reads `preprocess`/`separate_reference`/
    `features`/`align`/`pitch`'s already-finished output, never another
    aspect stage's (spec 6.10). `PipelineRunner` runs every member
    concurrently, each still in its own subprocess (spec 6.5's isolation
    guarantee is unaffected), with BLAS threads forced to 1 apiece for the
    duration -- 5 members x 4 threads each on a 4-vCPU box would make this
    slower than running them one at a time, not faster (spec 6.10's
    explicit warning).
    """

    stages: tuple[PipelineStage, ...]

    @property
    def name(self) -> str:
        return "+".join(stage.name for stage in self.stages)
