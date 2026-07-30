"""Stage 5: track pitch for both signals and score the user's accuracy
against the reference, note-for-note in cents (spec 6.3.5). The reference
curve is cached on the song (spec 6.6) once computed.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from vocalcoach.audio.io import read_mono
from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import (
    MIN_VOICED_FRACTION,
    PIANO_ROLL_OFF_PITCH_CENTS,
    PITCH_HOP_SECONDS,
    PITCH_SCORE_CENTS_FOR_ZERO,
    PITCH_TIMEOUT_SECONDS,
)
from vocalcoach.errors import NoVoiceDetected
from vocalcoach.models.audio import PianoRollData, PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import PitchDetector

STAGE_NAME = "pitch"


def _voiced_fraction(hz: list[float | None]) -> float:
    if not hz:
        return 0.0
    return sum(1 for value in hz if value is not None) / len(hz)


def _cents_deviation(user_hz: float, reference_hz: float) -> float:
    return 1200.0 * math.log2(user_hz / reference_hz)


def _score_from_mean_abs_cents(mean_abs_cents: float) -> float:
    fraction = min(1.0, mean_abs_cents / PITCH_SCORE_CENTS_FOR_ZERO)
    return round(100.0 * (1.0 - fraction), 1)


class PitchStage(PipelineStage):
    """`StageResult.data`: `score` (0-100), `mean_abs_cents`,
    `compared_frames`, `voiced_fraction`, `user_pitch_curve`,
    `reference_pitch_curve` -- both curves are carried here (not just
    looked up again from `context`/the song row) so stage 7's vibrato
    analysis has one place to read them from regardless of whether the
    reference curve came from cache or was just computed. `piano_roll`
    (a `PianoRollData`) is what the job handler persists into
    `analyses.pitch_curve_json` (spec 7, FR-31) -- unlike the two curves
    above, it is already resampled onto the user's time grid, ready for a
    frame-for-frame overlay.

    Does not write `songs.reference_pitch_json`/`vocal_stem_processed`
    itself: every stage instance is pickled across `PipelineRunner`'s
    spawn-based subprocess boundary (runner.py), and a `SongRepository`
    holding a live DB connection isn't picklable. The job handler persists
    `reference_pitch_curve` out of this stage's `StageResult` once the
    pipeline finishes, in the parent process where the real repository
    connection lives (spec 6.6 cache write).
    """

    name = STAGE_NAME
    timeout_seconds = PITCH_TIMEOUT_SECONDS

    def __init__(self, detector: PitchDetector) -> None:
        self._detector = detector

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        sample_rate = int(preprocess["sample_rate_hz"])

        reference_cached = context.vocal_stem_processed and context.reference_pitch is not None
        try:
            user_samples, _sr = read_mono(Path(preprocess["recording_path"]))
            user_hz = self._detector.detect(user_samples, sample_rate, PITCH_HOP_SECONDS)
            reference_curve = self._reference_pitch_curve(context, sample_rate)
        finally:
            self._detector.release()

        voiced_fraction = _voiced_fraction(user_hz)
        if voiced_fraction < MIN_VOICED_FRACTION:
            raise NoVoiceDetected(
                f"only {voiced_fraction:.1%} of the recording is voiced, "
                f"below the {MIN_VOICED_FRACTION:.0%} floor"
            )

        time_map = TimeMap.from_align_stage_data(context.result("align").data)
        aligned_reference_hz, deviations_cents = _align_and_compare(
            user_hz, reference_curve.hz, time_map
        )
        present_deviations = [c for c in deviations_cents if c is not None]
        mean_abs_cents = (
            sum(abs(c) for c in present_deviations) / len(present_deviations)
            if present_deviations
            else 0.0
        )
        score = _score_from_mean_abs_cents(mean_abs_cents) if present_deviations else 0.0

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
                "voiced_fraction": voiced_fraction,
                "user_pitch_curve": user_curve.model_dump(mode="json"),
                "reference_pitch_curve": reference_curve.model_dump(mode="json"),
                "reference_cached": reference_cached,
                "piano_roll": piano_roll.model_dump(mode="json"),
            },
        )

    def _reference_pitch_curve(self, context: AnalysisContext, sample_rate: int) -> PitchCurve:
        if context.vocal_stem_processed and context.reference_pitch is not None:
            return context.reference_pitch

        stem_path = Path(context.result("separate_reference").data["stem_path"])
        reference_samples, _sr = read_mono(stem_path)
        reference_hz = self._detector.detect(reference_samples, sample_rate, PITCH_HOP_SECONDS)
        return PitchCurve(hop_seconds=PITCH_HOP_SECONDS, hz=reference_hz)


def _align_and_compare(
    user_hz: list[float | None], reference_hz: list[float | None], time_map: TimeMap
) -> tuple[list[float | None], list[float | None]]:
    """Walks the user's pitch curve (its own, finer hop) and looks up each
    frame's counterpart in the reference curve through `time_map` -- stage
    4's warping path runs on a coarser MFCC hop, so frame indices are never
    compared directly, only the times they represent (spec 6.3.4/6.3.5).

    Returns, per user frame: the reference Hz value at that corresponding
    time (`None` if out of range or unvoiced there) and the signed cents
    deviation (`None` if either side is unvoiced/out of range). Both lists
    are the same length as `user_hz`, so this is also the single place the
    FR-31 piano-roll's frame-aligned overlay comes from -- the score
    (`mean_abs_cents`) and the visualization read the same comparison.
    """
    aligned_reference: list[float | None] = []
    deviations: list[float | None] = []
    for user_index, user_value in enumerate(user_hz):
        reference_time = time_map.user_to_reference(user_index * PITCH_HOP_SECONDS)
        reference_index = round(reference_time / PITCH_HOP_SECONDS)
        reference_value = (
            reference_hz[reference_index] if 0 <= reference_index < len(reference_hz) else None
        )
        aligned_reference.append(reference_value)
        if user_value is None or reference_value is None:
            deviations.append(None)
        else:
            deviations.append(_cents_deviation(user_value, reference_value))
    return aligned_reference, deviations
