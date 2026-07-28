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
    PITCH_HOP_SECONDS,
    PITCH_SCORE_CENTS_FOR_ZERO,
    PITCH_TIMEOUT_SECONDS,
)
from vocalcoach.errors import NoVoiceDetected
from vocalcoach.models.audio import PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import PitchDetector
from vocalcoach.repositories.interfaces import SongRepository

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
    reference curve came from cache or was just computed. The user curve is
    also what the runner persists into `analyses.pitch_curve_json` (spec 7,
    FR-31).
    """

    name = STAGE_NAME
    timeout_seconds = PITCH_TIMEOUT_SECONDS

    def __init__(self, detector: PitchDetector, songs: SongRepository) -> None:
        self._detector = detector
        self._songs = songs

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        sample_rate = int(preprocess["sample_rate_hz"])

        user_samples, _sr = read_mono(Path(preprocess["recording_path"]))
        user_hz = self._detector.detect(user_samples, sample_rate, PITCH_HOP_SECONDS)

        voiced_fraction = _voiced_fraction(user_hz)
        if voiced_fraction < MIN_VOICED_FRACTION:
            raise NoVoiceDetected(
                f"only {voiced_fraction:.1%} of the recording is voiced, "
                f"below the {MIN_VOICED_FRACTION:.0%} floor"
            )

        reference_curve = self._reference_pitch_curve(context, sample_rate)

        time_map = TimeMap.from_align_stage_data(context.result("align").data)
        deviations = _cents_deviations(user_hz, reference_curve.hz, time_map)
        mean_abs_cents = sum(abs(c) for c in deviations) / len(deviations) if deviations else 0.0
        score = _score_from_mean_abs_cents(mean_abs_cents) if deviations else 0.0

        user_curve = PitchCurve(hop_seconds=PITCH_HOP_SECONDS, hz=user_hz)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "score": score,
                "mean_abs_cents": mean_abs_cents,
                "compared_frames": len(deviations),
                "voiced_fraction": voiced_fraction,
                "user_pitch_curve": user_curve.model_dump(mode="json"),
                "reference_pitch_curve": reference_curve.model_dump(mode="json"),
            },
        )

    def _reference_pitch_curve(self, context: AnalysisContext, sample_rate: int) -> PitchCurve:
        if context.vocal_stem_processed and context.reference_pitch is not None:
            return context.reference_pitch

        stem_path = Path(context.result("separate_reference").data["stem_path"])
        reference_samples, _sr = read_mono(stem_path)
        reference_hz = self._detector.detect(reference_samples, sample_rate, PITCH_HOP_SECONDS)
        curve = PitchCurve(hop_seconds=PITCH_HOP_SECONDS, hz=reference_hz)
        self._songs.mark_vocal_stem_processed(context.song_id, curve)
        return curve


def _cents_deviations(
    user_hz: list[float | None], reference_hz: list[float | None], time_map: TimeMap
) -> list[float]:
    """Walks the user's pitch curve (its own, finer hop) and looks up each
    frame's counterpart in the reference curve through `time_map` -- stage
    4's warping path runs on a coarser MFCC hop, so frame indices are never
    compared directly, only the times they represent (spec 6.3.4/6.3.5).
    """
    deviations: list[float] = []
    for user_index, user_value in enumerate(user_hz):
        if user_value is None:
            continue
        reference_time = time_map.user_to_reference(user_index * PITCH_HOP_SECONDS)
        reference_index = round(reference_time / PITCH_HOP_SECONDS)
        if reference_index < 0 or reference_index >= len(reference_hz):
            continue
        reference_value = reference_hz[reference_index]
        if reference_value is None:
            continue
        deviations.append(_cents_deviation(user_value, reference_value))
    return deviations
