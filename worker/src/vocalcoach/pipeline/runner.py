"""`PipelineRunner`: orchestrates stage order, per-stage timeouts, transient
retries, and progress persistence (spec 12.3). It contains no DSP -- every
stage is opaque to it, and adding a new stage never requires editing this
file (spec 12.3 Open/Closed).

Every stage runs in its own child process. That is what makes a per-stage
timeout enforceable at all: a blocked native call (a stuck BLAS/torch
kernel) cannot be interrupted from inside the same process, only killed
from outside it. It is also what spec 6.5 asks for regardless: Demucs and
Whisper must never be resident together, and a model loaded inside a child
process is guaranteed gone the moment that process exits.
"""

from __future__ import annotations

import logging
import multiprocessing
import queue as queue_module
import time
from collections.abc import Callable, Sequence
from enum import StrEnum
from multiprocessing import get_context
from typing import TYPE_CHECKING, Protocol

from vocalcoach.constants import MAX_STAGE_RETRIES, RETRY_BACKOFF_BASE_SECONDS
from vocalcoach.errors import (
    InternalPipelineError,
    LogicalPipelineError,
    PipelineError,
    TransientPipelineError,
)
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult
from vocalcoach.pipeline.events import EventPublisher

if TYPE_CHECKING:
    from vocalcoach.pipeline.base import PipelineStage

logger = logging.getLogger(__name__)


class RunnerAnalysisRepository(Protocol):
    """The narrow slice of `AnalysisRepository` the runner itself needs
    (spec 12.2's "interfaces declared by the consumer" applied to Python):
    tracking which stage is running and persisting each one's result.
    Everything else on the full repository (scores, terminal states) is the
    job handler's concern, not the runner's.
    """

    def mark_processing(self, analysis_id: str, first_stage: str) -> None: ...

    def save_stage_progress(
        self, analysis_id: str, result: StageResult, next_stage: str | None
    ) -> None: ...


class RunOutcome(StrEnum):
    """What happened when `PipelineRunner.run` returned without raising."""

    #: Every remaining stage finished; the caller should mark the analysis done.
    COMPLETED = "completed"
    #: `should_stop` fired between stages; progress is saved, nothing else
    #: to do -- the job stays `processing` and XAUTOCLAIM picks it back up.
    INTERRUPTED = "interrupted"


def _stage_worker(
    stage: PipelineStage,
    context: AnalysisContext,
    result_queue: multiprocessing.Queue,  # type: ignore[type-arg]
) -> None:
    try:
        result_queue.put(("ok", stage.run(context)))
    except PipelineError as exc:
        result_queue.put(("error", exc))
    except Exception as exc:
        logger.exception("stage crashed", extra={"stage": stage.name})
        result_queue.put(("error", InternalPipelineError(f"stage '{stage.name}' crashed: {exc}")))


def _run_in_subprocess(stage: PipelineStage, context: AnalysisContext) -> StageResult:
    ctx = get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue()  # type: ignore[type-arg]
    process = ctx.Process(target=_stage_worker, args=(stage, context, result_queue))
    process.start()
    process.join(stage.timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
        raise TransientPipelineError(
            f"stage '{stage.name}' exceeded its {stage.timeout_seconds}s timeout",
            error_code="TIMEOUT",
        )

    try:
        kind, payload = result_queue.get_nowait()
    except queue_module.Empty as exc:
        exit_code = process.exitcode
        raise InternalPipelineError(
            f"stage '{stage.name}' produced no result (exit code {exit_code})"
        ) from exc

    if kind == "error":
        raise payload  # a PipelineError instance, already classified by the child
    return payload  # type: ignore[no-any-return]


class PipelineRunner:
    """Runs a fixed, ordered list of stages against one analysis."""

    def __init__(
        self,
        stages: Sequence[PipelineStage],
        analyses: RunnerAnalysisRepository,
        events: EventPublisher,
    ) -> None:
        self._stages = list(stages)
        self._analyses = analyses
        self._events = events

    def run(
        self,
        analysis_id: str,
        initial_context: AnalysisContext,
        already_done: dict[str, StageResult],
        should_stop: Callable[[], bool] = lambda: False,
    ) -> RunOutcome:
        """Runs every stage not already in `already_done` (spec 6.8: a
        retried job resumes from the first unfinished stage, not from
        zero). Raises `PipelineError` if a stage exhausts its retries or
        fails with a non-retryable error; the caller is responsible for
        `mark_failed` and the `failed` event, since it also owns the
        queue-level ack/retry decision (spec 10.1).
        """
        context = initial_context
        for result in already_done.values():
            context = context.with_result(result)

        total = len(self._stages)
        # 1-based position in the *full* stage order, for the WS `stage`
        # event -- not the position within `remaining`, so a resumed job
        # reports e.g. "6 of 10" rather than restarting the count from 1.
        stage_positions = {stage.name: i + 1 for i, stage in enumerate(self._stages)}
        remaining = [stage for stage in self._stages if stage.name not in already_done]

        if remaining:
            self._analyses.mark_processing(analysis_id, remaining[0].name)

        for index, stage in enumerate(remaining):
            if should_stop():
                logger.info(
                    "stopping between stages for graceful shutdown",
                    extra={"analysis_id": analysis_id, "next_stage": stage.name},
                )
                return RunOutcome.INTERRUPTED

            self._events.publish_stage(analysis_id, stage.name, stage_positions[stage.name], total)

            result = self._run_stage_with_retries(analysis_id, stage, context)

            context = context.with_result(result)
            next_stage = remaining[index + 1].name if index + 1 < len(remaining) else None
            self._analyses.save_stage_progress(analysis_id, result, next_stage)
            logger.info(
                "stage done",
                extra={
                    "analysis_id": analysis_id,
                    "stage": stage.name,
                    "duration_ms": result.duration_ms,
                },
            )

        return RunOutcome.COMPLETED

    def _run_stage_with_retries(
        self, analysis_id: str, stage: PipelineStage, context: AnalysisContext
    ) -> StageResult:
        attempt = 0
        while True:
            try:
                return _run_in_subprocess(stage, context)
            except LogicalPipelineError:
                raise
            except TransientPipelineError as exc:
                attempt += 1
                if attempt > MAX_STAGE_RETRIES:
                    raise
                delay = RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "stage failed, retrying",
                    extra={
                        "analysis_id": analysis_id,
                        "stage": stage.name,
                        "attempt": attempt,
                        "error": str(exc),
                        "retry_in_seconds": delay,
                    },
                )
                time.sleep(delay)
