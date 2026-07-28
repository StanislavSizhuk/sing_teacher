"""Wires one analysis job's full lifecycle: build its `AnalysisContext`,
run the pipeline, persist the terminal outcome, and report whether it is
safe to `XACK`.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from typing import Protocol

from vocalcoach.audio.paths import analysis_work_dir, recording_source_path, song_source_path
from vocalcoach.config import Settings
from vocalcoach.errors import PipelineError
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.records import AnalysisRecord, SongRecord
from vocalcoach.models.results import StageResult
from vocalcoach.pipeline.events import EventPublisher
from vocalcoach.pipeline.runner import RunOutcome

logger = logging.getLogger(__name__)


class Runner(Protocol):
    """The narrow slice of `PipelineRunner` the handler needs -- just
    enough to drive one job's stages and learn how it ended."""

    def run(
        self,
        analysis_id: str,
        initial_context: AnalysisContext,
        already_done: dict[str, StageResult],
        should_stop: Callable[[], bool],
    ) -> RunOutcome: ...


class HandlerAnalysisRepository(Protocol):
    """The narrow slice of `AnalysisRepository` the handler needs: reading
    a job's current state and recording its terminal outcome. Per-stage
    progress is `PipelineRunner`'s own concern (`RunnerAnalysisRepository`).
    """

    def get_by_id(self, analysis_id: str) -> AnalysisRecord: ...
    def mark_done(self, analysis_id: str, model_versions: dict[str, str]) -> None: ...
    def mark_failed(self, analysis_id: str, error_code: str) -> None: ...


class HandlerSongRepository(Protocol):
    """The narrow slice of `SongRepository` the handler needs -- just
    enough to build the `AnalysisContext` and check the cache flag during
    cleanup. Writing the cache (`save_lyrics`/`mark_vocal_stem_processed`)
    is each stage's own concern.
    """

    def get_by_id(self, song_id: str) -> SongRecord: ...


class AnalysisJobHandler:
    """One handler instance serves every job for the worker's lifetime;
    `handle` is re-entrant-safe to call repeatedly (a fresh `AnalysisContext`
    is built each time from the current, authoritative DB state).
    """

    def __init__(
        self,
        runner: Runner,
        analyses: HandlerAnalysisRepository,
        songs: HandlerSongRepository,
        events: EventPublisher,
        settings: Settings,
        model_versions: dict[str, str],
    ) -> None:
        self._runner = runner
        self._analyses = analyses
        self._songs = songs
        self._events = events
        self._settings = settings
        self._model_versions = model_versions

    def handle(self, analysis_id: str, should_stop: Callable[[], bool]) -> bool:
        """Returns `True` once the job reaches a terminal, durably-recorded
        state (safe to `XACK`); `False` if a graceful shutdown interrupted
        it between stages (leave pending -- the next worker instance's
        startup reclaim picks it back up, spec 10.1).
        """
        analysis = self._analyses.get_by_id(analysis_id)
        song = self._songs.get_by_id(analysis.song_id)
        context = self._build_context(analysis.id, analysis.user_id, song)

        try:
            outcome = self._runner.run(analysis_id, context, analysis.stages, should_stop)
        except PipelineError as exc:
            logger.warning(
                "analysis failed",
                extra={"analysis_id": analysis_id, "error_code": exc.error_code, "error": str(exc)},
            )
            self._analyses.mark_failed(analysis_id, exc.error_code)
            self._events.publish_failed(analysis_id, exc.error_code, str(exc))
            self._cleanup(context, recording_done=False)
            return True

        if outcome is RunOutcome.INTERRUPTED:
            return False

        self._analyses.mark_done(analysis_id, self._model_versions)
        self._events.publish_done(analysis_id)
        self._cleanup(context, recording_done=True)
        return True

    def mark_permanently_failed(self, analysis_id: str) -> None:
        """Called by the consumer when a job has been claimed more times
        than `MAX_CLAIM_ATTEMPTS` (spec 10.1) -- something about this job
        keeps crashing the worker itself, not a stage; give up without
        attempting to run the pipeline again.
        """
        self._analyses.mark_failed(analysis_id, "INTERNAL")
        self._events.publish_failed(
            analysis_id, "INTERNAL", "worker repeatedly failed to process this job"
        )

    def _build_context(self, analysis_id: str, user_id: str, song: SongRecord) -> AnalysisContext:
        return AnalysisContext(
            analysis_id=analysis_id,
            user_id=user_id,
            song_id=song.id,
            recording_path=recording_source_path(self._settings.audio_storage_dir, analysis_id),
            reference_path=song_source_path(self._settings.audio_storage_dir, song.id),
            work_dir=analysis_work_dir(self._settings.audio_storage_dir, analysis_id),
            song_content_hash=song.content_hash,
            vocal_stem_processed=song.vocal_stem_processed,
            reference_lyrics=song.lyrics,
            reference_pitch=song.reference_pitch,
            pitch_engine=self._settings.pitch_engine,
            whisper_model=self._settings.whisper_model,
            demucs_model=self._settings.demucs_model,
            model_weights_dir=self._settings.model_weights_dir,
        )

    def _cleanup(self, context: AnalysisContext, *, recording_done: bool) -> None:
        shutil.rmtree(context.work_dir, ignore_errors=True)

        if recording_done:
            # FR-43: the recording is only needed for a possible retry
            # while the analysis can still fail; once it's done, delete it now
            # rather than waiting for the interim age-based sweep.
            context.recording_path.unlink(missing_ok=True)

        # Re-read rather than trust context's copy: this job may be the one
        # that just flipped the flag in stage 5, and once it's true no
        # future analysis of this song ever reads the original upload again.
        song = self._songs.get_by_id(context.song_id)
        if song.vocal_stem_processed:
            context.reference_path.unlink(missing_ok=True)
