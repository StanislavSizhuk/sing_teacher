"""Stage A8 (spec 6.8): corrects for the user singing in a different key
than the reference -- capo, transposed chords, or just a more comfortable
range. Without this, an otherwise perfect performance a few semitones off
the reference's absolute pitch would score close to zero.

Runs in both modes (unlike A4/A5, this is not a mode switch): whether a
shift actually gets *applied* is a data-driven decision this stage makes
internally from `context.mode`/`context.allow_transposition` plus the
measured shift's own size and stability -- reading those fields for that
decision is ordinary business logic, not the `if mode ==` stage-selection
branching spec 12.3 forbids (that concern is `PipelineStage.modes`, which
this stage does not narrow).

**Why the conditions are strict** (spec 6.8): this is the single most
dangerous correction in the pipeline. Without limits it would forgive a
singer who is simply, consistently flat. A small, stable, semitone-scale
offset is a transposition; a wandering or barely-there one is just
intonation, and must still be scored as such.
"""

from __future__ import annotations

import time

import numpy as np

from vocalcoach.constants import KEY_NORMALIZATION_TIMEOUT_SECONDS
from vocalcoach.dsp.pitch_scoring import score_from_mean_abs_cents
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "key_normalization"

_SEMITONE_CENTS = 100.0


class KeyNormalizationStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `applied` (bool), `key_shift_semitones` (the
    applied shift, `None` if not applied -- spec 7's `analyses.
    key_shift_semitones` is exactly this), `median_semitones`/
    `iqr_semitones` (the raw measurement, always present for observability
    even when not applied), `out_of_range` (feeds `KEY_SHIFT_OUT_OF_RANGE`,
    spec 6.15), `adjusted_score`/`adjusted_mean_abs_cents` (the pitch
    aspect's score recomputed with the shift removed, `None` unless
    `applied`) -- `AggregateStage` substitutes this for stage `"pitch"`'s
    own score when present (spec 6.8's whole point: the shift must actually
    change the pitch score, not just get reported).
    """

    name = STAGE_NAME
    timeout_seconds = KEY_NORMALIZATION_TIMEOUT_SECONDS

    def __init__(
        self, min_semitones: float, max_iqr_semitones: float, max_semitones: float
    ) -> None:
        self._min_semitones = min_semitones
        self._max_iqr_semitones = max_iqr_semitones
        self._max_semitones = max_semitones

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        deviation_cents: list[float | None] = context.result("pitch").data["piano_roll"][
            "deviation_cents"
        ]
        present_cents = [c for c in deviation_cents if c is not None]

        if not present_cents:
            return self._result(start, applied=False, median_semitones=0.0, iqr_semitones=0.0)

        median_semitones = float(np.median(present_cents)) / _SEMITONE_CENTS
        iqr_semitones = (
            float(np.percentile(present_cents, 75) - np.percentile(present_cents, 25))
            / _SEMITONE_CENTS
        )

        eligible = context.mode == "mixed" or context.allow_transposition
        out_of_range = eligible and abs(median_semitones) > self._max_semitones
        applied = (
            eligible
            and not out_of_range
            and abs(median_semitones) >= self._min_semitones
            and iqr_semitones <= self._max_iqr_semitones
        )

        adjusted_score = None
        adjusted_mean_abs_cents = None
        if applied:
            shift_cents = median_semitones * _SEMITONE_CENTS
            adjusted = [c - shift_cents for c in present_cents]
            adjusted_mean_abs_cents = sum(abs(c) for c in adjusted) / len(adjusted)
            adjusted_score = score_from_mean_abs_cents(adjusted_mean_abs_cents)

        return self._result(
            start,
            applied=applied,
            median_semitones=median_semitones,
            iqr_semitones=iqr_semitones,
            out_of_range=out_of_range,
            adjusted_score=adjusted_score,
            adjusted_mean_abs_cents=adjusted_mean_abs_cents,
        )

    def _result(
        self,
        start: float,
        *,
        applied: bool,
        median_semitones: float,
        iqr_semitones: float,
        out_of_range: bool = False,
        adjusted_score: float | None = None,
        adjusted_mean_abs_cents: float | None = None,
    ) -> StageResult:
        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "applied": applied,
                "key_shift_semitones": round(median_semitones, 2) if applied else None,
                "median_semitones": round(median_semitones, 3),
                "iqr_semitones": round(iqr_semitones, 3),
                "out_of_range": out_of_range,
                "adjusted_score": adjusted_score,
                "adjusted_mean_abs_cents": adjusted_mean_abs_cents,
            },
        )
