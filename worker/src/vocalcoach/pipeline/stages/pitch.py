"""Stage A5 (`clean` only, spec 6.5): track pitch for the user's recording
directly (pyworld/CREPE/pYIN) and score its accuracy against the reference,
note-for-note in cents. `mixed` gets its F0 curve a different way --
`MelodyPitchStage` (A4, `pipeline/stages/melody.py`) -- and writes its
result under the same stage name (`"pitch"`), so aggregate/vibrato/
persistence never need to know or care which engine actually ran (spec
12.3: the runner picks the stage by `modes`, not an `if mode ==` inside one).

The reference curve itself is cold-path output (spec 6.4 P4, M2) -- already
cached on the song and detected exactly once, ever, before this stage's song
ever reaches `ready`; this stage only ever reads it, never (re)computes it.
"""

from __future__ import annotations

import time
from pathlib import Path

from vocalcoach.audio.io import read_mono
from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import (
    MIN_VOICED_FRACTION,
    PIANO_ROLL_OFF_PITCH_CENTS,
    PITCH_HOP_SECONDS,
    PITCH_TIMEOUT_SECONDS,
)
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.dsp.pitch_detection import detect_gated
from vocalcoach.dsp.pitch_scoring import (
    align_and_compare,
    score_from_mean_abs_cents,
    voiced_fraction,
)
from vocalcoach.errors import NoVoiceDetected
from vocalcoach.models.audio import PianoRollData, PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import PitchDetector

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

    def __init__(self, detector: PitchDetector) -> None:
        self._detector = detector

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        sample_rate = int(preprocess["sample_rate_hz"])

        features = load_shared_features(Path(context.result("features").data["features_path"]))
        try:
            user_samples, _sr = read_mono(Path(preprocess["recording_path"]))
            user_hz = detect_gated(
                self._detector, user_samples, sample_rate, PITCH_HOP_SECONDS, features.user.rms_fine
            )
        finally:
            self._detector.release()

        fraction = voiced_fraction(user_hz)
        if fraction < MIN_VOICED_FRACTION:
            raise NoVoiceDetected(
                f"only {fraction:.1%} of the recording is voiced, "
                f"below the {MIN_VOICED_FRACTION:.0%} floor"
            )

        time_map = TimeMap.from_align_stage_data(context.result("align").data)
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
