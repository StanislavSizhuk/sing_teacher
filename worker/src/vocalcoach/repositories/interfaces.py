"""Repository interfaces the pipeline stages and runner depend on.

Declared here, implemented in `postgres.py` -- consumer-declared
interfaces, mirroring how `api/internal/service` does it on the Go side
(spec 12.2/12.3: DB access through repositories, never raw SQL in a stage
or in the runner).
"""

from __future__ import annotations

from typing import Protocol

from vocalcoach.models.audio import Lyrics, PitchCurve
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

    def mark_processing(self, analysis_id: str, first_stage: str) -> None:
        """Transitions `queued` -> `processing` when a worker picks the job
        up, and records the first stage it is about to run."""
        ...

    def save_stage_progress(
        self, analysis_id: str, result: StageResult, next_stage: str | None
    ) -> None:
        """Merges `result` into `stages_json` under its stage name (spec
        6.1) and sets `current_stage` to `next_stage` (or clears it once
        there is no next stage), so a poll or WS push sees progress live."""
        ...

    def save_aspect_score(self, analysis_id: str, aspect: str, score: float) -> None:
        """Writes one of `pitch_score`/`rhythm_score`/.../`timbre_score`
        (spec 7); `aspect` must be one of `config.ASPECTS`."""
        ...

    def save_pitch_curve(self, analysis_id: str, curve: PitchCurve) -> None: ...

    def mark_done(self, analysis_id: str, model_versions: dict[str, str]) -> None:
        """Terminal success: every stage 1-10 finished (spec 18/E3 -- the
        weighted overall score and text report are stage 11, built in E4)."""
        ...

    def mark_failed(self, analysis_id: str, error_code: str) -> None:
        """Terminal failure: a logical error, or a transient one that
        exhausted its retries (spec 6.8)."""
        ...
