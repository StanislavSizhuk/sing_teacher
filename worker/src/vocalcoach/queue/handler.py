"""Wires one analysis job's full lifecycle: build its `AnalysisContext`,
run the pipeline, persist the terminal outcome, and report whether it is
safe to `XACK`. By the time a job reaches here its song's cold path has
already finished (spec 6.2, M2) -- the scheduler never hands this handler
an analysis whose song isn't `ready`, so the reference vocal stem and
pitch curve are always already cached (spec 6.6).
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from typing import Protocol

from vocalcoach.audio.paths import analysis_work_dir, recording_source_path
from vocalcoach.config import ASPECTS, Settings
from vocalcoach.errors import InternalPipelineError, PipelineError
from vocalcoach.models.audio import PianoRollData, PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.mode import Mode
from vocalcoach.models.records import AnalysisRecord, SongRecord
from vocalcoach.models.results import StageResult
from vocalcoach.pipeline.events import EventPublisher
from vocalcoach.pipeline.runner import ProgressReporter, RunOutcome

logger = logging.getLogger(__name__)


class Runner(Protocol):
    """The narrow slice of `PipelineRunner` the handler needs -- just
    enough to drive one job's stages and learn how it ended."""

    def run(
        self,
        job_id: str,
        initial_context: AnalysisContext,
        already_done: dict[str, StageResult],
        progress: ProgressReporter,
        should_stop: Callable[[], bool],
        mode: Mode | None = None,
    ) -> RunOutcome: ...


class HandlerAnalysisRepository(Protocol):
    """The narrow slice of `AnalysisRepository` the handler needs: tracking
    per-stage progress (spec 12.2's "interfaces declared by the consumer"
    applied to Python -- this module is `PipelineRunner`'s `ProgressReporter`
    consumer for analysis jobs), reading a job's current state, denormalizing
    each aspect stage's score out of `stages_json` into its own column once
    the run completes, and recording the terminal outcome.
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
    def get_by_id(self, analysis_id: str) -> AnalysisRecord: ...
    def save_aspect_score(self, analysis_id: str, aspect: str, score: float) -> None: ...
    def save_piano_roll(self, analysis_id: str, data: PianoRollData) -> None: ...
    def save_user_pitch_curve(self, analysis_id: str, curve: PitchCurve) -> None: ...
    def prune_dense_stage_fields(self, analysis_id: str) -> None: ...
    def save_scoring_result(
        self, analysis_id: str, overall_score: float, feedback_text: str, scoring_version: str
    ) -> None: ...
    def record_progress_snapshot(
        self, analysis_id: str, user_id: str, overall_score: float
    ) -> None: ...
    def mark_done(self, analysis_id: str, model_versions: dict[str, str]) -> None: ...
    def mark_failed(self, analysis_id: str, error_code: str) -> None: ...


class HandlerSongRepository(Protocol):
    """The slice of `SongRepository` the handler needs to build the
    `AnalysisContext` from a song's already-cached cold-path output (spec
    6.6). The handler never writes to it -- the cold path's own
    `SongPrepJobHandler` owns every write to `songs`.
    """

    def get_by_id(self, song_id: str) -> SongRecord: ...


class AnalysisProgressReporter:
    """Adapts `HandlerAnalysisRepository`'s analysis_id-taking methods to
    `PipelineRunner`'s job-kind-agnostic `ProgressReporter` (spec 12.1 DRY --
    keeps the id-binding here instead of changing the repository's own,
    already id-scoped signatures).
    """

    def __init__(self, repo: HandlerAnalysisRepository, analysis_id: str) -> None:
        self._repo = repo
        self._analysis_id = analysis_id

    def mark_processing(self, first_stage: str, stage_index: int, total_stages: int) -> None:
        self._repo.mark_processing(self._analysis_id, first_stage, stage_index, total_stages)

    def save_stage_progress(
        self,
        result: StageResult,
        next_stage: str | None,
        next_stage_index: int | None,
        total_stages: int,
    ) -> None:
        self._repo.save_stage_progress(
            self._analysis_id, result, next_stage, next_stage_index, total_stages
        )


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
        # Deliberately outside the try/except below: a missing cached
        # reference here means the scheduler handed this handler an
        # analysis whose song was not actually ready (spec 10.2), a bug
        # elsewhere rather than a property of this analysis's own input --
        # let it propagate uncaught so the job stays pending for reclaim
        # (spec 10.1) instead of being recorded as a normal analysis failure.
        context = self._build_context(analysis.id, analysis.user_id, analysis.song_id)
        progress = AnalysisProgressReporter(self._analyses, analysis_id)

        try:
            outcome = self._runner.run(
                analysis_id, context, analysis.stages, progress, should_stop, mode=context.mode
            )
        except PipelineError as exc:
            logger.warning(
                "analysis failed",
                extra={"analysis_id": analysis_id, "error_code": exc.error_code, "error": str(exc)},
            )
            self._analyses.mark_failed(analysis_id, exc.error_code)
            self._events.publish_failed(analysis_id, exc.error_code, str(exc))
            # A failed analysis stays retryable (FR-26) without re-uploading
            # anything, which depends on already-completed stages' cached
            # results in stages_json still resolving to real files --
            # preprocess writes its canonical recording.wav into work_dir,
            # so removing work_dir here would make the very next retry
            # crash trying to open a file this run just deleted.
            self._cleanup(context, recording_done=False, remove_work_dir=False)
            return True

        if outcome is RunOutcome.INTERRUPTED:
            return False

        self._persist_scores(analysis_id)
        # Once the dense curves above are durably saved into their own
        # bytea/JSONB columns, stages_json no longer needs to carry them too
        # (spec 7.3) -- stages_json's per-stage write exists for mid-run
        # resumability (spec 6.8), which a `done` analysis never needs again.
        self._analyses.prune_dense_stage_fields(analysis_id)
        self._analyses.mark_done(analysis_id, self._model_versions)
        self._events.publish_done(analysis_id)
        self._cleanup(context, recording_done=True, remove_work_dir=True)
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
        `analyses.pitch_curve_json` the same way (spec 7, FR-31), its dense
        `user_pitch_curve` into `analyses.user_pitch` as packed bytes (spec
        7.3), and stage 11's `overall_score`/`feedback_text`/`scoring_version`
        go into their own columns (spec 6.4, FR-32), and `overall_score`
        also lands as one `progress_snapshots` row for the FR-35 progress
        chart.

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
        if pitch_result is not None and "user_pitch_curve" in pitch_result.data:
            user_pitch = PitchCurve.model_validate(pitch_result.data["user_pitch_curve"])
            self._analyses.save_user_pitch_curve(analysis_id, user_pitch)

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

    def _build_context(self, analysis_id: str, user_id: str, song_id: str) -> AnalysisContext:
        song = self._songs.get_by_id(song_id)
        if song.vocal_stem_path is None or song.reference_pitch is None:
            # The scheduler only ever hands this handler an analysis whose
            # song reached prep_status='ready' (spec 10.2) -- reaching here
            # means that invariant broke somewhere upstream, not a property
            # of this analysis's own input, so it is worth a retry rather
            # than an immediate logical failure.
            raise InternalPipelineError(
                f"song {song_id} has no cached reference (vocal_stem_path/reference_pitch); "
                "expected prep_status='ready' before an analysis reaches analyses:run"
            )
        return AnalysisContext(
            analysis_id=analysis_id,
            user_id=user_id,
            song_id=song.id,
            recording_path=recording_source_path(self._settings.audio_storage_dir, analysis_id),
            work_dir=analysis_work_dir(self._settings.audio_storage_dir, analysis_id),
            reference_vocal_stem_path=song.vocal_stem_path,
            reference_pitch=song.reference_pitch,
            reference_lyrics=song.lyrics,
            # `AnalysisRecord` carries no `mode`/`allow_transposition` yet --
            # FR-27's mode selector is `POST /analyses` and `analyses.mode`
            # (spec 8.3), which M4 (spec 18) wires end to end. Until then
            # every analysis runs the `clean` path (this context's own
            # field default), matching FR-27's documented default anyway.
        )

    def _cleanup(
        self, context: AnalysisContext, *, recording_done: bool, remove_work_dir: bool
    ) -> None:
        if remove_work_dir:
            shutil.rmtree(context.work_dir, ignore_errors=True)

        if recording_done:
            # FR-43: the recording is only needed for a possible retry
            # while the analysis can still fail; once it's done, delete it now
            # rather than waiting for the interim age-based sweep.
            context.recording_path.unlink(missing_ok=True)
