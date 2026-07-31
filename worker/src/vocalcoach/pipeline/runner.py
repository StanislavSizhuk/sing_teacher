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

`ParallelGroup` entries (spec 6.10) run their members concurrently, each
still in its own subprocess -- see `_run_group_with_retries`.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import queue as queue_module
import time
from collections.abc import Callable, Sequence
from enum import StrEnum
from multiprocessing import get_context
from typing import Protocol

from vocalcoach.constants import MAX_STAGE_RETRIES, RETRY_BACKOFF_BASE_SECONDS
from vocalcoach.errors import (
    InternalPipelineError,
    LogicalPipelineError,
    PipelineError,
    TransientPipelineError,
)
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult
from vocalcoach.pipeline.base import ParallelGroup, PipelineStage
from vocalcoach.pipeline.events import EventPublisher

logger = logging.getLogger(__name__)

# Every env var numpy's BLAS backend reads its thread pool size from (spec
# 6.10, mirroring runtime/threads.py's own list). Forced to "1" per task
# only while a ParallelGroup's members are actually running concurrently.
_BLAS_THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


class RunnerAnalysisRepository(Protocol):
    """The narrow slice of `AnalysisRepository` the runner itself needs
    (spec 12.2's "interfaces declared by the consumer" applied to Python):
    tracking which stage is running and persisting each one's result.
    Everything else on the full repository (scores, terminal states) is the
    job handler's concern, not the runner's.
    """

    def mark_processing(
        self, analysis_id: str, first_stage: str, stage_index: int, total_stages: int
    ) -> None: ...

    def save_stage_progress(
        self,
        analysis_id: str,
        result: StageResult,
        next_stage: str | None,
        next_stage_index: int | None,
        total_stages: int,
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


def _join_subprocess(
    stage: PipelineStage,
    process: multiprocessing.process.BaseProcess,
    result_queue: multiprocessing.Queue,  # type: ignore[type-arg]
) -> StageResult | PipelineError:
    """Waits for one already-started stage subprocess and returns either its
    result or the error it failed with -- never raises, so a caller
    collecting several of these (a `ParallelGroup`) can gather every
    member's outcome before deciding what to do about any of them.
    """
    process.join(stage.timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
        return TransientPipelineError(
            f"stage '{stage.name}' exceeded its {stage.timeout_seconds}s timeout",
            error_code="TIMEOUT",
        )

    try:
        kind, payload = result_queue.get_nowait()
    except queue_module.Empty:
        exit_code = process.exitcode
        return InternalPipelineError(
            f"stage '{stage.name}' produced no result (exit code {exit_code})"
        )

    if kind == "error":
        return payload  # type: ignore[no-any-return]
    return payload  # type: ignore[no-any-return]


def _run_in_subprocess(stage: PipelineStage, context: AnalysisContext) -> StageResult:
    ctx = get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue()  # type: ignore[type-arg]
    process = ctx.Process(target=_stage_worker, args=(stage, context, result_queue))
    process.start()

    outcome = _join_subprocess(stage, process, result_queue)
    if isinstance(outcome, PipelineError):
        raise outcome
    return outcome


def _force_single_threaded_blas() -> dict[str, str | None]:
    """Sets every BLAS thread env var to 1 in this (parent) process, right
    before spawning a `ParallelGroup`'s members -- each inherits this
    environment at spawn time (spec 6.10). Returns the prior values so the
    caller can restore them once every member has been started; already-
    spawned children keep their own inherited copy regardless.
    """
    original = {var: os.environ.get(var) for var in _BLAS_THREAD_ENV_VARS}
    for var in _BLAS_THREAD_ENV_VARS:
        os.environ[var] = "1"
    return original


def _restore_env(original: dict[str, str | None]) -> None:
    for var, value in original.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value


def _start_group(
    group: ParallelGroup, context: AnalysisContext
) -> dict[str, StageResult | PipelineError]:
    ctx = get_context("spawn")
    processes = []
    queues: list[multiprocessing.Queue] = []  # type: ignore[type-arg]

    original_env = _force_single_threaded_blas()
    try:
        for stage in group.stages:
            result_queue: multiprocessing.Queue = ctx.Queue()  # type: ignore[type-arg]
            process = ctx.Process(target=_stage_worker, args=(stage, context, result_queue))
            process.start()
            processes.append(process)
            queues.append(result_queue)
    finally:
        _restore_env(original_env)

    return {
        stage.name: _join_subprocess(stage, process, result_queue)
        for stage, process, result_queue in zip(group.stages, processes, queues, strict=True)
    }


def _flatten(entries: Sequence[PipelineStage | ParallelGroup]) -> list[PipelineStage]:
    flat: list[PipelineStage] = []
    for entry in entries:
        if isinstance(entry, ParallelGroup):
            flat.extend(entry.stages)
        else:
            flat.append(entry)
    return flat


def _remaining_entries(
    entries: Sequence[PipelineStage | ParallelGroup], already_done: dict[str, StageResult]
) -> list[PipelineStage | ParallelGroup]:
    remaining: list[PipelineStage | ParallelGroup] = []
    for entry in entries:
        if isinstance(entry, ParallelGroup):
            members = tuple(stage for stage in entry.stages if stage.name not in already_done)
            if members:
                remaining.append(ParallelGroup(members))
        elif entry.name not in already_done:
            remaining.append(entry)
    return remaining


class PipelineRunner:
    """Runs a fixed, ordered list of stages (or `ParallelGroup`s of them)
    against one analysis."""

    def __init__(
        self,
        stages: Sequence[PipelineStage | ParallelGroup],
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

        # Positions/total are numbered over the *flattened* stage order
        # (spec 8.5's WS `stage` event reports "N of total" per stage, group
        # membership is an execution detail the client never sees) -- a
        # ParallelGroup counts as however many stages it contains, not one.
        flat_all = _flatten(self._stages)
        total = len(flat_all)
        stage_positions = {stage.name: i + 1 for i, stage in enumerate(flat_all)}

        remaining_entries = _remaining_entries(self._stages, already_done)
        remaining_flat = _flatten(remaining_entries)

        if remaining_flat:
            first = remaining_flat[0]
            self._analyses.mark_processing(
                analysis_id, first.name, stage_positions[first.name], total
            )

        flat_position = 0
        for entry in remaining_entries:
            if should_stop():
                next_name = remaining_flat[flat_position].name
                logger.info(
                    "stopping between stages for graceful shutdown",
                    extra={"analysis_id": analysis_id, "next_stage": next_name},
                )
                return RunOutcome.INTERRUPTED

            members = self._members(entry)
            for stage in members:
                self._events.publish_stage(
                    analysis_id, stage.name, stage_positions[stage.name], total
                )

            results = self._run_entry_with_retries(analysis_id, entry, context)

            for stage in members:
                result = results[stage.name]
                context = context.with_result(result)
                flat_position += 1
                next_stage = (
                    remaining_flat[flat_position].name
                    if flat_position < len(remaining_flat)
                    else None
                )
                next_stage_index = stage_positions[next_stage] if next_stage is not None else None
                self._analyses.save_stage_progress(
                    analysis_id, result, next_stage, next_stage_index, total
                )
                logger.info(
                    "stage done",
                    extra={
                        "analysis_id": analysis_id,
                        "stage": result.stage,
                        "duration_ms": result.duration_ms,
                    },
                )

        return RunOutcome.COMPLETED

    @staticmethod
    def _members(entry: PipelineStage | ParallelGroup) -> tuple[PipelineStage, ...]:
        return entry.stages if isinstance(entry, ParallelGroup) else (entry,)

    def _run_entry_with_retries(
        self, analysis_id: str, entry: PipelineStage | ParallelGroup, context: AnalysisContext
    ) -> dict[str, StageResult]:
        if isinstance(entry, ParallelGroup):
            return self._run_group_with_retries(analysis_id, entry, context)
        return {entry.name: self._run_stage_with_retries(analysis_id, entry, context)}

    def _run_group_with_retries(
        self, analysis_id: str, group: ParallelGroup, context: AnalysisContext
    ) -> dict[str, StageResult]:
        outcomes = _start_group(group, context)

        results: dict[str, StageResult] = {}
        for stage in group.stages:
            outcome = outcomes[stage.name]
            if isinstance(outcome, StageResult):
                results[stage.name] = outcome
                continue
            if isinstance(outcome, LogicalPipelineError):
                raise outcome
            # TransientPipelineError: the concurrent run failed for this one
            # member alone -- retry it by itself, on the same backoff policy
            # a non-parallel stage would get, rather than re-running the
            # whole (mostly-succeeded) group.
            logger.warning(
                "parallel stage failed, retrying alone",
                extra={"analysis_id": analysis_id, "stage": stage.name, "error": str(outcome)},
            )
            results[stage.name] = self._run_stage_with_retries(analysis_id, stage, context)
        return results

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
