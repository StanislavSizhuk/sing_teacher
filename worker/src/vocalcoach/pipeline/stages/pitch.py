"""Stage A5 (`clean` only, spec 6.5): score the user's pitch accuracy
against the reference, note-for-note in cents. `mixed` gets its F0 curve a
different way -- `MelodyPitchStage` (A4, `pipeline/stages/melody.py`) --
and writes its result under the same stage name (`"pitch"`), so
aggregate/vibrato/persistence never need to know or care which engine
actually ran (spec 12.3: the runner picks the stage by `modes`, not an
`if mode ==` inside one).

ADR-0033: the user's F0 curve itself is extracted by `align` (A3), not
here -- align needs it first, to align on melody rather than MFCC, so
this stage just reads `context.result("align").data["user_pitch_curve"]`
back instead of re-running the same detector a second time. The reference
curve is cold-path output (spec 6.4 P4, M2) -- already cached on the
song and detected exactly once, ever, before this stage's song ever
reaches `ready`; this stage only ever reads it, never (re)computes it.
"""

from __future__ import annotations

import time

from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import (
    PIANO_ROLL_OFF_PITCH_CENTS,
    PITCH_HOP_SECONDS,
    PITCH_TIMEOUT_SECONDS,
)
from vocalcoach.dsp.pitch_scoring import (
    align_and_compare,
    score_from_mean_abs_cents,
    voiced_fraction,
)
from vocalcoach.models.audio import PianoRollData, PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "pitch"


class PitchStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `score` (0-100), `mean_abs_cents`,
    `compared_frames`, `voiced_fraction`, `user_pitch_curve` (the reference
    curve is not repeated here -- it never changes per-analysis, callers
    needing it read `context.reference_pitch` directly). `piano_roll` (a
    `PianoRollData`) is what the job handler persists into
    `analyses.pitch_curve_json` (spec 7, FR-31) -- unlike `user_pitch_curve`,
    it is already resampled onto the user's time grid, ready for a
    frame-for-frame overlay.
    """

    name = STAGE_NAME
    timeout_seconds = PITCH_TIMEOUT_SECONDS
    modes = frozenset({"clean"})

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        align_result = context.result("align").data
        user_hz: list[float | None] = align_result["user_pitch_curve"]["hz"]
        fraction = voiced_fraction(user_hz)

        time_map = TimeMap.from_align_stage_data(align_result)
        aligned_reference_hz, deviations_cents = align_and_compare(
            user_hz, context.reference_pitch.hz, time_map, PITCH_HOP_SECONDS
        )
        present_deviations = [c for c in deviations_cents if c is not None]
        mean_abs_cents = (
            sum(abs(c) for c in present_deviations) / len(present_deviations)
            if present_deviations
            else 0.0
        )
        score = score_from_mean_abs_cents(mean_abs_cents) if present_deviations else 0.0

        user_curve = PitchCurve(hop_seconds=PITCH_HOP_SECONDS, hz=user_hz)
        piano_roll = PianoRollData(
            hop_seconds=PITCH_HOP_SECONDS,
            user_hz=user_hz,
            reference_hz=aligned_reference_hz,
            deviation_cents=deviations_cents,
            off_pitch=[
                c is not None and abs(c) > PIANO_ROLL_OFF_PITCH_CENTS for c in deviations_cents
            ],
        )

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "score": score,
                "mean_abs_cents": mean_abs_cents,
                "compared_frames": len(present_deviations),
                "voiced_fraction": fraction,
                "user_pitch_curve": user_curve.model_dump(mode="json"),
                "piano_roll": piano_roll.model_dump(mode="json"),
            },
        )
