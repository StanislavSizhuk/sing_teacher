"""Wires one song's cold-path lifecycle (spec 6.2, 6.4, M2): build its
`SongPrepContext`, run the P-stage pipeline, persist the terminal outcome,
and wake any analyses that were waiting on this song's reference (spec
10.3, FR-16/17).
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from vocalcoach.audio.paths import song_prep_work_dir, song_source_path, song_stem_path
from vocalcoach.config import Settings
from vocalcoach.errors import PipelineError
from vocalcoach.models.audio import Lyrics, PitchCurve
from vocalcoach.models.context import SongPrepContext
from vocalcoach.models.records import SongRecord
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.events import EventPublisher
from vocalcoach.pipeline.runner import ProgressReporter, RunOutcome
from vocalcoach.queue.streams import ANALYSES_STREAM_NAME

logger = logging.getLogger(__name__)


class AnalysesQueuePublisher(Protocol):
    """The narrow slice of `redis.Redis` this handler needs: publishing a
    woken analysis onto analyses:run (spec 10.3) -- declared here rather
    than depending on the concrete client (spec 12.2's "interfaces
    declared by the consumer" applied to Python). `fields: Any`: redis-py's
    own `xadd` stub unions many key/value types dict's invariance can't
    line up against a narrower one; this consumer only ever passes
    `dict[str, str]`, enforced at the one call site, not by this signature.
    """

    def xadd(self, name: str, fields: Any) -> object: ...


class Runner(Protocol):
    """The narrow slice of `PipelineRunner` this handler needs -- just
    enough to drive one song's P-stages and learn how it ended."""

    def run(
        self,
        job_id: str,
        initial_context: SongPrepContext,
        already_done: dict[str, StageResult],
        progress: ProgressReporter,
        should_stop: Callable[[], bool],
    ) -> RunOutcome: ...


class HandlerSongPrepRepository(Protocol):
    """The narrow slice of `SongRepository` this handler needs: tracking
    per-P-stage progress (this module's `ProgressReporter` consumer, spec
    12.2), reading a song's current prep state, and recording the terminal
    outcome (spec 6.4, 7)."""

    def get_by_id(self, song_id: str) -> SongRecord: ...
    def mark_prep_processing(
        self, song_id: str, first_stage: str, stage_index: int, total_stages: int
    ) -> None: ...
    def save_prep_stage_progress(
        self,
        song_id: str,
        result: StageResult,
        next_stage: str | None,
        next_stage_index: int | None,
        total_stages: int,
    ) -> None: ...
    def mark_prep_ready(
        self,
        song_id: str,
        *,
        vocal_stem_path: Path,
        reference_pitch: PitchCurve,
        lyrics: Lyrics | None,
        lyrics_available: bool,
    ) -> None: ...
    def mark_prep_failed(self, song_id: str, error_code: str) -> None: ...


class HandlerWakeRepository(Protocol):
    """The narrow slice of `AnalysisRepository` this handler needs to wake
    or fail every analysis waiting on a song's cold path (spec 10.3, FR-16/17)."""

    def wake_waiting_for_reference(self, song_id: str) -> tuple[list[str], dict[str, int]]: ...
    def fail_waiting_for_reference(self, song_id: str, error_code: str) -> list[str]: ...


class SongPrepProgressReporter:
    """Adapts `HandlerSongPrepRepository`'s song_id-taking methods to
    `PipelineRunner`'s job-kind-agnostic `ProgressReporter` -- mirrors
    `queue.handler.AnalysisProgressReporter` exactly (spec 12.1 DRY: same
    small adapter shape, bound to a different repository/id)."""

    def __init__(self, repo: HandlerSongPrepRepository, song_id: str) -> None:
        self._repo = repo
        self._song_id = song_id

    def mark_processing(self, first_stage: str, stage_index: int, total_stages: int) -> None:
        self._repo.mark_prep_processing(self._song_id, first_stage, stage_index, total_stages)

    def save_stage_progress(
        self,
        result: StageResult,
        next_stage: str | None,
        next_stage_index: int | None,
        total_stages: int,
    ) -> None:
        self._repo.save_prep_stage_progress(
            self._song_id, result, next_stage, next_stage_index, total_stages
        )


class SongPrepJobHandler:
    """One handler instance serves every song-prep job for the worker's
    lifetime; `handle` is re-entrant-safe to call repeatedly (a fresh
    `SongPrepContext` is built each time from the current, authoritative DB
    state).
    """

    def __init__(
        self,
        runner: Runner,
        songs: HandlerSongPrepRepository,
        analyses: HandlerWakeRepository,
        events: EventPublisher,
        redis_client: AnalysesQueuePublisher,
        settings: Settings,
    ) -> None:
        self._runner = runner
        self._songs = songs
        self._analyses = analyses
        self._events = events
        self._redis = redis_client
        self._settings = settings

    def handle(self, song_id: str, should_stop: Callable[[], bool]) -> bool:
        """Returns `True` once the job reaches a terminal, durably-recorded
        state (safe to `XACK`); `False` if a graceful shutdown interrupted
        it between stages (leave pending -- the next worker instance's
        startup reclaim picks it back up, spec 10.1).
        """
        song = self._songs.get_by_id(song_id)
        context = self._build_context(song_id)
        progress = SongPrepProgressReporter(self._songs, song_id)

        try:
            outcome = self._runner.run(song_id, context, song.prep_stages, progress, should_stop)
        except PipelineError as exc:
            logger.warning(
                "song prep failed",
                extra={"song_id": song_id, "error_code": exc.error_code, "error": str(exc)},
            )
            self._songs.mark_prep_failed(song_id, exc.error_code)
            self._wake_failed(song_id, exc.error_code)
            # Left in place, same rationale as AnalysisJobHandler: a
            # restarted prep (POST /songs/{id}/prepare) resumes from
            # already-completed P-stages by reopening exactly these files.
            self._cleanup(context, remove_work_dir=False)
            return True

        if outcome is RunOutcome.INTERRUPTED:
            return False

        self._persist_ready(song_id, context)
        self._wake_ready(song_id)
        self._cleanup(context, remove_work_dir=True)
        return True

    def mark_permanently_failed(self, song_id: str) -> None:
        """Called by the consumer when a job has been claimed more times
        than `MAX_CLAIM_ATTEMPTS` (spec 10.1) -- something about this job
        keeps crashing the worker itself, not a stage; give up without
        attempting to run the pipeline again.
        """
        self._songs.mark_prep_failed(song_id, "INTERNAL")
        self._wake_failed(song_id, "INTERNAL")

    def _persist_ready(self, song_id: str, context: SongPrepContext) -> None:
        """Terminal success (spec 6.4): P1/P2/P4 (required) all finished,
        P3 (transcription) optionally so (FR-18) -- re-reads the durably
        saved `prep_stages_json` rather than trusting the local `context`
        for stage-produced data, mirroring
        `AnalysisJobHandler._persist_scores`'s same rationale.
        """
        record = self._songs.get_by_id(song_id)
        pitch_result = record.prep_stages["prep_reference_pitch"]
        reference_pitch = PitchCurve.model_validate(pitch_result.data["reference_pitch_curve"])

        transcribe_result = record.prep_stages.get("transcribe")
        lyrics_available = (
            transcribe_result is not None and transcribe_result.status == StageStatus.DONE
        )
        lyrics = (
            Lyrics.model_validate(transcribe_result.data["lyrics"])
            if lyrics_available and transcribe_result is not None
            else None
        )

        self._songs.mark_prep_ready(
            song_id,
            vocal_stem_path=context.vocal_stem_path,
            reference_pitch=reference_pitch,
            lyrics=lyrics,
            lyrics_available=lyrics_available,
        )

    def _wake_ready(self, song_id: str) -> None:
        newly_queued, positions = self._analyses.wake_waiting_for_reference(song_id)
        for analysis_id in newly_queued:
            self._redis.xadd(ANALYSES_STREAM_NAME, {"job_id": analysis_id})
        for analysis_id, position in positions.items():
            self._events.publish_queued(analysis_id, position)

    def _wake_failed(self, song_id: str, error_code: str) -> None:
        failed_ids = self._analyses.fail_waiting_for_reference(song_id, error_code)
        for analysis_id in failed_ids:
            self._events.publish_failed(
                analysis_id, error_code, "reference preparation failed for this song"
            )

    def _build_context(self, song_id: str) -> SongPrepContext:
        return SongPrepContext(
            song_id=song_id,
            reference_path=song_source_path(self._settings.audio_storage_dir, song_id),
            work_dir=song_prep_work_dir(self._settings.audio_storage_dir, song_id),
            vocal_stem_path=song_stem_path(self._settings.song_stems_dir, song_id),
        )

    def _cleanup(self, context: SongPrepContext, *, remove_work_dir: bool) -> None:
        if not remove_work_dir:
            # Failed: a restarted prep (POST /songs/{id}/prepare, FR-17)
            # re-reads this same server-managed path for P1, so it must
            # survive -- same rationale as AnalysisJobHandler keeping the
            # recording around while an analysis can still be retried.
            return

        shutil.rmtree(context.work_dir, ignore_errors=True)
        # Every P-stage's needed output is now durably cached elsewhere
        # (vocal_stem_path, reference_pitch); no future analysis of this
        # song, nor a future prep run, ever reads the raw upload again
        # (spec 6.6, FR-43).
        context.reference_path.unlink(missing_ok=True)
