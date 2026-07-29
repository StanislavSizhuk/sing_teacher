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
from vocalcoach.config import ASPECTS, Settings
from vocalcoach.errors import PipelineError
from vocalcoach.models.audio import PianoRollData
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
    a job's current state, denormalizing each aspect stage's score out of
    `stages_json` into its own column once the run completes, and recording
    the terminal outcome. Per-stage progress is `PipelineRunner`'s own
    concern (`RunnerAnalysisRepository`).
    """

    def get_by_id(self, analysis_id: str) -> AnalysisRecord: ...
    def save_aspect_score(self, analysis_id: str, aspect: str, score: float) -> None: ...
    def save_piano_roll(self, analysis_id: str, data: PianoRollData) -> None: ...
    def save_scoring_result(
        self, analysis_id: str, overall_score: float, feedback_text: str, scoring_version: str
    ) -> None: ...
    def record_progress_snapshot(
        self, analysis_id: str, user_id: str, overall_score: float
    ) -> None: ...
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

        self._persist_scores(analysis_id)
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

    def _persist_scores(self, analysis_id: str) -> None:
        """Denormalizes each aspect stage's `data["score"]` out of
        `stages_json` into its own column (`analyses.pitch_score`, etc.) --
        stage names equal `config.ASPECTS` exactly by construction, so no
        separate mapping table is needed. `pitch`'s `piano_roll` goes into
        `analyses.pitch_curve_json` the same way (spec 7, FR-31), and
        stage 11's `overall_score`/`feedback_text`/`scoring_version` go into
        their own columns (spec 6.4, FR-32), and `overall_score` also lands
        as one `progress_snapshots` row for the FR-35 progress chart.

        `PipelineRunner` never does this itself: it stays agnostic of which
        stages happen to produce a score, so adding a stage there never
        needs a runner change (spec 12.3 Open/Closed) -- this is where that
        stage-specific knowledge is allowed to live instead.
        """
        record = self._analyses.get_by_id(analysis_id)
        for aspect in ASPECTS:
            result = record.stages.get(aspect)
            if result is not None and "score" in result.data:
                self._analyses.save_aspect_score(analysis_id, aspect, float(result.data["score"]))

        pitch_result = record.stages.get("pitch")
        if pitch_result is not None and "piano_roll" in pitch_result.data:
            piano_roll = PianoRollData.model_validate(pitch_result.data["piano_roll"])
            self._analyses.save_piano_roll(analysis_id, piano_roll)

        aggregate_result = record.stages.get("aggregate")
        if aggregate_result is not None:
            data = aggregate_result.data
            overall_score = float(data["overall_score"])
            self._analyses.save_scoring_result(
                analysis_id,
                overall_score=overall_score,
                feedback_text=str(data["feedback_text"]),
                scoring_version=str(data["scoring_version"]),
            )
            # FR-35/G4: one progress-chart point per analysis, recorded here
            # rather than in AggregateStage itself -- stage 11 has no
            # database access (spec 12.3), and record.user_id is only known
            # to the handler, not the pipeline context.
            self._analyses.record_progress_snapshot(analysis_id, record.user_id, overall_score)

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
