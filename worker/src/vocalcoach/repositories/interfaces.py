"""Repository interfaces the pipeline stages and runner depend on.

Declared here, implemented in `postgres.py` -- consumer-declared
interfaces, mirroring how `api/internal/service` does it on the Go side
(spec 12.2/12.3: DB access through repositories, never raw SQL in a stage
or in the runner).
"""

from __future__ import annotations

from typing import Protocol

from vocalcoach.models.audio import Lyrics, PianoRollData, PitchCurve
from vocalcoach.models.records import AnalysisRecord, SongRecord
from vocalcoach.models.results import StageResult


class SongRepository(Protocol):
    """The reference-song cache (spec 6.6): a vocal stem, once separated and
    transcribed, is reused by every later analysis of the same song."""

    def get_by_id(self, song_id: str) -> SongRecord: ...

    def save_lyrics(self, song_id: str, lyrics: Lyrics) -> None:
        """Persists stage 3's transcript. Independent of
        `mark_vocal_stem_processed`: the cache flag only flips once stage
        5's reference pitch curve is also ready (spec 6.6)."""
        ...

    def mark_vocal_stem_processed(self, song_id: str, reference_pitch: PitchCurve) -> None:
        """Persists the reference pitch curve and flips
        `vocal_stem_processed` in one write -- this is the flag spec 6.6
        checks to skip stages 2, 3 and the reference pitch curve for the
        next analysis of this song."""
        ...


class AnalysisRepository(Protocol):
    """One analysis job's status, progress and per-aspect results (spec 7)."""

    def get_by_id(self, analysis_id: str) -> AnalysisRecord: ...

    def mark_processing(
        self, analysis_id: str, first_stage: str, stage_index: int, total_stages: int
    ) -> None:
        """Transitions `queued` -> `processing` when a worker picks the job
        up, and records the first stage it is about to run: its name,
        1-based position among total_stages, and a fresh
        current_stage_started_at so the client can render a live elapsed
        timer instead of a static label (spec 6.2, 8.3)."""
        ...

    def save_stage_progress(
        self,
        analysis_id: str,
        result: StageResult,
        next_stage: str | None,
        next_stage_index: int | None,
        total_stages: int,
    ) -> None:
        """Merges `result` into `stages_json` under its stage name (spec
        6.1) and sets `current_stage`/`current_stage_index` to `next_stage`
        (or clears them, and stops the elapsed timer, once there is no next
        stage), so a poll or WS push sees progress live."""
        ...

    def save_aspect_score(self, analysis_id: str, aspect: str, score: float) -> None:
        """Writes one of `pitch_score`/`rhythm_score`/.../`timbre_score`
        (spec 7); `aspect` must be one of `config.ASPECTS`."""
        ...

    def save_piano_roll(self, analysis_id: str, data: PianoRollData) -> None:
        """Persists FR-31's frame-aligned overlay data into
        `analyses.pitch_curve_json` (spec 7)."""
        ...

    def save_scoring_result(
        self, analysis_id: str, overall_score: float, feedback_text: str, scoring_version: str
    ) -> None:
        """Writes stage 11's weighted `overall_score`, the FR-32 text
        report, and the `scoring_version` it was computed under (spec 6.4)
        -- called once every aspect stage's own score is already
        denormalized into its own column."""
        ...

    def record_progress_snapshot(
        self, analysis_id: str, user_id: str, overall_score: float
    ) -> None:
        """Upserts the FR-35 progress-chart point for this analysis into
        `progress_snapshots` (spec 7), keyed on `analysis_id` so a job that
        fails and later succeeds on retry updates its one point instead of
        the chart gaining a duplicate for the same job (spec 6.8)."""
        ...

    def mark_done(self, analysis_id: str, model_versions: dict[str, str]) -> None:
        """Terminal success: every stage 1-11 finished (spec 18/E4)."""
        ...

    def mark_failed(self, analysis_id: str, error_code: str) -> None:
        """Terminal failure: a logical error, or a transient one that
        exhausted its retries (spec 6.8)."""
        ...
