"""`PipelineRunner`: orchestrates stage order, per-stage timeouts, transient
retries, optional-stage skipping, and progress persistence (spec 12.3). It
contains no DSP -- every stage is opaque to it, and adding a new stage
never requires editing this file (spec 12.3 Open/Closed).

Generic over `ContextT` (`pipeline.base.PipelineContext`) so the same
runner drives both the warm path (`AnalysisContext`) and the cold path
(`SongPrepContext`, spec 6.2/6.4, M2) -- retries, timeouts and subprocess
isolation are identical for both, only the job-specific progress writes
differ, which is exactly what `ProgressReporter` isolates (spec 12.1 DRY).

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
from typing import Generic, Protocol

from vocalcoach.constants import MAX_STAGE_RETRIES, RETRY_BACKOFF_BASE_SECONDS
from vocalcoach.errors import (
    InternalPipelineError,
    LogicalPipelineError,
    PipelineError,
    TransientPipelineError,
)
from vocalcoach.models.mode import Mode
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import ContextT, ParallelGroup, PipelineStage
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


class ProgressReporter(Protocol):
    """The narrow slice of progress-tracking a job kind needs (spec 12.2's
    "interfaces declared by the consumer" applied to Python): which stage is
    running, and persisting each one's result. Deliberately carries no job
    id -- one instance is already bound to a single job (an
    `AnalysisProgressReporter`/`SongPrepProgressReporter` adapter closing
    over its analysis_id/song_id), which is what lets `PipelineRunner`
    itself stay job-kind-agnostic. Terminal-state handling (scores,
    `prep_status`, `ready`/`failed`) is each job handler's own concern, not
    the runner's.
    """

    def mark_processing(self, first_stage: str, stage_index: int, total_stages: int) -> None: ...

    def save_stage_progress(
        self,
        result: StageResult,
        next_stage: str | None,
        next_stage_index: int | None,
        total_stages: int,
    ) -> None: ...


class RunOutcome(StrEnum):
    """What happened when `PipelineRunner.run` returned without raising."""

    #: Every remaining stage finished; the caller should mark the job done.
    COMPLETED = "completed"
    #: `should_stop` fired between stages; progress is saved, nothing else
    #: to do -- the job stays `processing` and XAUTOCLAIM picks it back up.
    INTERRUPTED = "interrupted"


def _stage_worker(
    stage: PipelineStage[ContextT],
    context: ContextT,
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
    stage: PipelineStage[ContextT],
    process: multiprocessing.process.BaseProcess,
    result_queue: multiprocessing.Queue,  # type: ignore[type-arg]
) -> StageResult | PipelineError:
    """Waits for one already-started stage subprocess and returns either its
    result or the error it failed with -- never raises, so a caller
    collecting several of these (a `ParallelGroup`) can gather every
    member's outcome before deciding what to do about any of them.

    Reads the queue *before* joining the process, deliberately -- a
    `multiprocessing.Queue` feeds pickled data into an OS pipe from a
    background thread in the child, and that pipe has a small fixed buffer
    (~64KB on Linux). `StageResult.data` for a full song's pitch curve at a
    10ms hop easily serializes past a few hundred KB, so a child that
    `put()`s one and then exits blocks on the write once the pipe fills,
    and never actually exits. `process.join(timeout)` called first -- as
    this used to do -- waits for exactly that exit, so parent and child
    deadlock until the timeout fires and kills the child: every run looked
    like it "exceeded" its budget at precisely the configured number of
    seconds regardless of the value, because nothing was ever slow, the
    pipe was just never drained. `Queue.get(timeout=...)` actively drains
    it, so the child's write unblocks and it exits promptly right after.
    """
    try:
        _kind, payload = result_queue.get(timeout=stage.timeout_seconds)
    except queue_module.Empty:
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
        exit_code = process.exitcode
        return InternalPipelineError(
            f"stage '{stage.name}' produced no result (exit code {exit_code})"
        )

    process.join()
    return payload  # type: ignore[no-any-return]


def _run_in_subprocess(stage: PipelineStage[ContextT], context: ContextT) -> StageResult:
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
    group: ParallelGroup[ContextT], context: ContextT
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


def _flatten(
    entries: Sequence[PipelineStage[ContextT] | ParallelGroup[ContextT]],
) -> list[PipelineStage[ContextT]]:
    flat: list[PipelineStage[ContextT]] = []
    for entry in entries:
        if isinstance(entry, ParallelGroup):
            flat.extend(entry.stages)
        else:
            flat.append(entry)
    return flat


def _remaining_entries(
    entries: Sequence[PipelineStage[ContextT] | ParallelGroup[ContextT]],
    already_done: dict[str, StageResult],
) -> list[PipelineStage[ContextT] | ParallelGroup[ContextT]]:
    remaining: list[PipelineStage[ContextT] | ParallelGroup[ContextT]] = []
    for entry in entries:
        if isinstance(entry, ParallelGroup):
            members = tuple(stage for stage in entry.stages if stage.name not in already_done)
            if members:
                remaining.append(ParallelGroup(members))
        elif entry.name not in already_done:
            remaining.append(entry)
    return remaining


def _stages_for_mode(
    entries: Sequence[PipelineStage[ContextT] | ParallelGroup[ContextT]],
    mode: Mode | None,
) -> list[PipelineStage[ContextT] | ParallelGroup[ContextT]]:
    """Drops any stage whose `modes` (spec 12.3) does not include `mode` --
    e.g. A5 (`PitchStage`, `clean`-only) or A4 (melody extraction,
    `mixed`-only, spec 6.5 table). `mode=None` (the cold path, which has no
    concept of clean/mixed) runs every stage unfiltered. A `ParallelGroup`
    with some members dropped keeps its remaining members; one with none
    left is dropped entirely, same shape as `_remaining_entries` above.
    """
    if mode is None:
        return list(entries)

    filtered: list[PipelineStage[ContextT] | ParallelGroup[ContextT]] = []
    for entry in entries:
        if isinstance(entry, ParallelGroup):
            members = tuple(stage for stage in entry.stages if mode in stage.modes)
            if members:
                filtered.append(ParallelGroup(members))
        elif mode in entry.modes:
            filtered.append(entry)
    return filtered


# Generic[ContextT] (not PEP 695's `class Foo[T]`), same reason as
# pipeline/base.py's PipelineStage/ParallelGroup: this instance's ContextT
# must stay tied to it across __init__ and every method (run,
# _run_entry_with_retries, ...), which requires the class itself, not each
# method independently, to carry the type parameter.
class PipelineRunner(Generic[ContextT]):  # noqa: UP046
    """Runs a fixed, ordered list of stages (or `ParallelGroup`s of them)
    against one job -- an analysis (warm path) or a song's cold path."""

    def __init__(
        self,
        stages: Sequence[PipelineStage[ContextT] | ParallelGroup[ContextT]],
        events: EventPublisher,
    ) -> None:
        self._stages = list(stages)
        self._events = events

    def run(
        self,
        job_id: str,
        initial_context: ContextT,
        already_done: dict[str, StageResult],
        progress: ProgressReporter,
        should_stop: Callable[[], bool] = lambda: False,
        mode: Mode | None = None,
    ) -> RunOutcome:
        """Runs every stage not already in `already_done` (spec 6.8: a
        retried job resumes from the first unfinished stage, not from
        zero). Raises `PipelineError` if a required stage exhausts its
        retries or fails with a non-retryable error; the caller is
        responsible for marking the job failed and publishing the failure
        event, since it also owns the queue-level ack/retry decision (spec
        10.1). An optional stage (`required = False`) never raises -- see
        `_run_stage_with_retries`.

        `mode` (spec 12.3) drops any stage whose own `modes` does not
        include it before anything else runs -- the cold path (no
        clean/mixed concept) always passes `None`, meaning unfiltered.
        """
        context = initial_context
        for result in already_done.values():
            context = context.with_result(result)

        active_stages = _stages_for_mode(self._stages, mode)

        # Positions/total are numbered over the *flattened* stage order
        # (spec 8.5's WS `stage` event reports "N of total" per stage, group
        # membership is an execution detail the client never sees) -- a
        # ParallelGroup counts as however many stages it contains, not one.
        flat_all = _flatten(active_stages)
        total = len(flat_all)
        stage_positions = {stage.name: i + 1 for i, stage in enumerate(flat_all)}

        remaining_entries = _remaining_entries(active_stages, already_done)
        remaining_flat = _flatten(remaining_entries)

        if remaining_flat:
            first = remaining_flat[0]
            progress.mark_processing(first.name, stage_positions[first.name], total)

        flat_position = 0
        for entry in remaining_entries:
            if should_stop():
                next_name = remaining_flat[flat_position].name
                logger.info(
                    "stopping between stages for graceful shutdown",
                    extra={"job_id": job_id, "next_stage": next_name},
                )
                return RunOutcome.INTERRUPTED

            members = self._members(entry)
            for stage in members:
                self._events.publish_stage(job_id, stage.name, stage_positions[stage.name], total)

            results = self._run_entry_with_retries(job_id, entry, context)

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
                progress.save_stage_progress(result, next_stage, next_stage_index, total)
                logger.info(
                    "stage done",
                    extra={
                        "job_id": job_id,
                        "stage": result.stage,
                        "status": str(result.status),
                        "duration_ms": result.duration_ms,
                    },
                )

        return RunOutcome.COMPLETED

    @staticmethod
    def _members(
        entry: PipelineStage[ContextT] | ParallelGroup[ContextT],
    ) -> tuple[PipelineStage[ContextT], ...]:
        return entry.stages if isinstance(entry, ParallelGroup) else (entry,)

    def _run_entry_with_retries(
        self,
        job_id: str,
        entry: PipelineStage[ContextT] | ParallelGroup[ContextT],
        context: ContextT,
    ) -> dict[str, StageResult]:
        if isinstance(entry, ParallelGroup):
            return self._run_group_with_retries(job_id, entry, context)
        return {entry.name: self._run_stage_with_retries(job_id, entry, context)}

    def _run_group_with_retries(
        self, job_id: str, group: ParallelGroup[ContextT], context: ContextT
    ) -> dict[str, StageResult]:
        outcomes = _start_group(group, context)

        results: dict[str, StageResult] = {}
        for stage in group.stages:
            outcome = outcomes[stage.name]
            if isinstance(outcome, StageResult):
                results[stage.name] = outcome
                continue
            if isinstance(outcome, LogicalPipelineError):
                if not stage.required:
                    results[stage.name] = self._skipped_result(job_id, stage, outcome)
                    continue
                raise outcome
            # TransientPipelineError: the concurrent run failed for this one
            # member alone -- retry it by itself, on the same backoff policy
            # a non-parallel stage would get, rather than re-running the
            # whole (mostly-succeeded) group.
            logger.warning(
                "parallel stage failed, retrying alone",
                extra={"job_id": job_id, "stage": stage.name, "error": str(outcome)},
            )
            results[stage.name] = self._run_stage_with_retries(job_id, stage, context)
        return results

    def _run_stage_with_retries(
        self, job_id: str, stage: PipelineStage[ContextT], context: ContextT
    ) -> StageResult:
        attempt = 0
        while True:
            try:
                return _run_in_subprocess(stage, context)
            except LogicalPipelineError as exc:
                if not stage.required:
                    return self._skipped_result(job_id, stage, exc)
                raise
            except TransientPipelineError as exc:
                attempt += 1
                if attempt > MAX_STAGE_RETRIES:
                    if not stage.required:
                        return self._skipped_result(job_id, stage, exc)
                    raise
                delay = RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "stage failed, retrying",
                    extra={
                        "job_id": job_id,
                        "stage": stage.name,
                        "attempt": attempt,
                        "error": str(exc),
                        "retry_in_seconds": delay,
                    },
                )
                time.sleep(delay)

    @staticmethod
    def _skipped_result(
        job_id: str, stage: PipelineStage[ContextT], exc: PipelineError
    ) -> StageResult:
        """Spec 6.3: an optional stage's failure never aborts the pipeline
        -- it is recorded as `SKIPPED` with the reason (FR-18), and every
        stage depending on its output must treat that output as absent."""
        logger.warning(
            "optional stage failed, skipping",
            extra={
                "job_id": job_id,
                "stage": stage.name,
                "error_code": exc.error_code,
                "error": str(exc),
            },
        )
        return StageResult(
            stage=stage.name,
            status=StageStatus.SKIPPED,
            duration_ms=0,
            error_code=exc.error_code,
            error_message=str(exc),
        )
