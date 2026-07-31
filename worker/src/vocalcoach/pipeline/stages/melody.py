"""Stage A4 (`mixed` only, spec 6.5/6.6): extracts the vocal F0 curve
directly from the user's mixture (voice + accompaniment) instead of running
a pitch detector meant for a monophonic signal -- Demucs never runs on the
user's own recording (spec 2.3, ADR-0003), so there is no isolated vocal
stem to hand a normal pitch detector here the way `clean` has.

Writes its result under the *same* stage name `PitchStage` (A5) uses --
`"pitch"` -- never both in the same run (`modes` below is disjoint from
`PitchStage.modes`), so aggregate, `VibratoStage`, and the job handler's
score persistence all read `context.result("pitch")`/`stages_json["pitch"]`
without ever branching on which engine actually produced it (spec 12.3).
"""

from __future__ import annotations

import time
from pathlib import Path

from vocalcoach.audio.io import read_mono
from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import (
    MELODY_HOP_SECONDS,
    MELODY_TIMEOUT_SECONDS,
    MIN_VOICED_FRACTION,
    PIANO_ROLL_OFF_PITCH_CENTS,
)
from vocalcoach.dsp.melody import extract_melody
from vocalcoach.dsp.pitch_scoring import (
    align_and_compare,
    score_from_mean_abs_cents,
    voiced_fraction,
)
from vocalcoach.errors import MelodyExtractionFailed
from vocalcoach.models.audio import PianoRollData, PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "pitch"


class MelodyPitchStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: identical shape to `PitchStage`'s -- `score`,
    `mean_abs_cents`, `compared_frames`, `voiced_fraction`,
    `user_pitch_curve`, `piano_roll`. Every downstream reader of `"pitch"`
    stays unaware this came from `dsp/melody.py` rather than a monophonic
    pitch detector (spec 12.3).
    """

    name = STAGE_NAME
    timeout_seconds = MELODY_TIMEOUT_SECONDS
    modes = frozenset({"mixed"})

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        sample_rate = int(preprocess["sample_rate_hz"])

        mixture, _sr = read_mono(Path(preprocess["recording_path"]))
        user_hz = extract_melody(mixture, sample_rate, MELODY_HOP_SECONDS)

        fraction = voiced_fraction(user_hz)
        if fraction < MIN_VOICED_FRACTION:
            raise MelodyExtractionFailed(
                f"only {fraction:.1%} of the recording had a confident melody "
                f"estimate, below the {MIN_VOICED_FRACTION:.0%} floor"
            )

        time_map = TimeMap.from_align_stage_data(context.result("align").data)
        aligned_reference_hz, deviations_cents = align_and_compare(
            user_hz, context.reference_pitch.hz, time_map, MELODY_HOP_SECONDS
        )
        present_deviations = [c for c in deviations_cents if c is not None]
        mean_abs_cents = (
            sum(abs(c) for c in present_deviations) / len(present_deviations)
            if present_deviations
            else 0.0
        )
        score = score_from_mean_abs_cents(mean_abs_cents) if present_deviations else 0.0

        user_curve = PitchCurve(hop_seconds=MELODY_HOP_SECONDS, hz=user_hz)
        piano_roll = PianoRollData(
            hop_seconds=MELODY_HOP_SECONDS,
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
