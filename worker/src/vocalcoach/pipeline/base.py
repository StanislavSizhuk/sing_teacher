"""Stage contract every pipeline stage implements (spec 6.1, 12.3).

Adding a stage means adding a class here and listing it in the runner's
stage order -- `PipelineRunner` itself never changes (Open/Closed, spec
12.3: "adding a new stage must not require editing the runner").

Generic over `ContextT` so the exact same `PipelineStage`/`ParallelGroup`/
`PipelineRunner` machinery drives both the warm path (`AnalysisContext`)
and the cold path (`SongPrepContext`, spec 6.2/6.4, M2) -- retries,
per-stage timeouts, subprocess isolation and the optional-stage contract
below are identical for both, so writing them twice would violate spec
12.1's DRY rule.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, Protocol, Self, TypeVar

from vocalcoach.models.results import StageResult


class PipelineContext(Protocol):
    """The narrow shape `PipelineRunner` needs from any context it drives:
    look up a prior stage's result, and return a copy with a new one
    recorded. `AnalysisContext`/`SongPrepContext` satisfy this structurally.
    """

    def result(self, stage_name: str) -> StageResult: ...

    def with_result(self, result: StageResult) -> Self: ...


ContextT = TypeVar("ContextT", bound=PipelineContext)


# ContextT stays a plain, importable module-level TypeVar rather than PEP
# 695's `class Foo[T]` syntax: runner.py's standalone subprocess-boundary
# functions (_stage_worker et al.) are generic over this exact same
# TypeVar too, which PEP 695's per-class-scoped type parameters can't be
# shared across a module boundary for.
class PipelineStage(ABC, Generic[ContextT]):  # noqa: UP046
    """One step of the ML pipeline (spec 6.2 table)."""

    #: Stable identifier stored in `analyses.stages_json`/`prep_stages_json`
    #: and `current_stage`/`prep_stage`.
    name: str
    #: Wall-clock budget for `run` (spec 6.2), enforced by `PipelineRunner`
    #: from outside this class -- a stage never times itself.
    timeout_seconds: int
    #: Whether a failure here is fatal to the whole run (spec 6.3). An
    #: optional stage (e.g. P3 transcription, FR-18) that exhausts its
    #: retries or fails logically does not abort the pipeline: the runner
    #: records a `StageStatus.SKIPPED` result instead and moves on. Default
    #: True preserves every stage's existing behavior unless it opts out.
    required: bool = True

    @abstractmethod
    def run(self, context: ContextT) -> StageResult:
        """Executes this stage against `context` and returns its result.

        Must be idempotent and side-effect-free beyond `context` and the
        database (spec 6.1): running it twice on the same input produces
        the same output, so a retried job can safely re-run a stage that
        never got to persist its result.
        """
        raise NotImplementedError


@dataclass(frozen=True)
class ParallelGroup(Generic[ContextT]):  # noqa: UP046 -- see PipelineStage above
    """A batch of stages with no dependency on each other -- every aspect
    stage past alignment only reads `preprocess`/`features`/`align`/
    `pitch`'s already-finished output, never another aspect stage's (spec
    6.10). `PipelineRunner` runs every member concurrently, each still in
    its own subprocess (spec 6.5's isolation guarantee is unaffected), with
    BLAS threads forced to 1 apiece for the duration -- 5 members x 4
    threads each on a 4-vCPU box would make this slower than running them
    one at a time, not faster (spec 6.10's explicit warning).
    """

    stages: tuple[PipelineStage[ContextT], ...]

    @property
    def name(self) -> str:
        return "+".join(stage.name for stage in self.stages)
