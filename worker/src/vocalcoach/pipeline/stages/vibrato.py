"""Stage 7: detect vibrato rate and depth from each sustained-pitch run in
both pitch curves and score how closely the user's matches the reference's
(spec 6.3.7).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from vocalcoach.constants import (
    VIBRATO_AUTOCORR_PEAK_THRESHOLD,
    VIBRATO_DEPTH_TOLERANCE_CENTS,
    VIBRATO_MAX_RATE_HZ,
    VIBRATO_MIN_DEPTH_CENTS,
    VIBRATO_MIN_RATE_HZ,
    VIBRATO_MIN_SEGMENT_SECONDS,
    VIBRATO_PRESENCE_MISMATCH_SCORE,
    VIBRATO_RATE_TOLERANCE_HZ,
    VIBRATO_TIMEOUT_SECONDS,
)
from vocalcoach.models.audio import PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "vibrato"


@dataclass(frozen=True)
class VibratoProfile:
    detected: bool
    rate_hz: float | None = None
    depth_cents: float | None = None


def _sustained_runs(curve: PitchCurve, min_seconds: float) -> list[list[float]]:
    """Contiguous voiced runs at least `min_seconds` long -- vibrato is
    only meaningful on a held note, not across a pitch glide or a rest."""
    min_frames = max(1, round(min_seconds / curve.hop_seconds))
    runs: list[list[float]] = []
    current: list[float] = []
    for value in curve.hz:
        if value is not None:
            current.append(value)
        else:
            if len(current) >= min_frames:
                runs.append(current)
            current = []
    if len(current) >= min_frames:
        runs.append(current)
    return runs


def _dominant_oscillation(cents: np.ndarray, hop_seconds: float) -> tuple[float, float] | None:
    """Finds a periodic oscillation in the vibrato rate band via
    autocorrelation; `None` if nothing periodic and deep enough stands out.
    """
    detrended = cents - cents.mean()
    if len(detrended) < 4:
        return None

    autocorr = np.correlate(detrended, detrended, mode="full")
    autocorr = autocorr[len(autocorr) // 2 :]
    if autocorr[0] <= 0:
        return None
    autocorr = autocorr / autocorr[0]

    min_lag = max(1, round(1.0 / VIBRATO_MAX_RATE_HZ / hop_seconds))
    max_lag = min(len(autocorr) - 1, round(1.0 / VIBRATO_MIN_RATE_HZ / hop_seconds))
    if min_lag >= max_lag:
        return None

    window = autocorr[min_lag : max_lag + 1]
    peak_offset = int(np.argmax(window))
    peak_lag = min_lag + peak_offset
    if window[peak_offset] < VIBRATO_AUTOCORR_PEAK_THRESHOLD:
        return None

    rate_hz = 1.0 / (peak_lag * hop_seconds)
    depth_cents = float(np.percentile(detrended, 95) - np.percentile(detrended, 5))
    if depth_cents < VIBRATO_MIN_DEPTH_CENTS:
        return None
    return rate_hz, depth_cents


def _profile(curve: PitchCurve) -> VibratoProfile:
    detections: list[tuple[float, float, int]] = []
    for run in _sustained_runs(curve, VIBRATO_MIN_SEGMENT_SECONDS):
        cents = 1200.0 * np.log2(np.asarray(run) / np.median(run))
        found = _dominant_oscillation(cents, curve.hop_seconds)
        if found is not None:
            rate, depth = found
            detections.append((rate, depth, len(run)))

    if not detections:
        return VibratoProfile(detected=False)

    total_weight = sum(weight for _, _, weight in detections)
    avg_rate = sum(rate * weight for rate, _, weight in detections) / total_weight
    avg_depth = sum(depth * weight for _, depth, weight in detections) / total_weight
    return VibratoProfile(detected=True, rate_hz=avg_rate, depth_cents=avg_depth)


def _score(user: VibratoProfile, reference: VibratoProfile) -> float:
    if not reference.detected and not user.detected:
        return 100.0
    if reference.detected != user.detected:
        return VIBRATO_PRESENCE_MISMATCH_SCORE
    if (
        user.rate_hz is None
        or reference.rate_hz is None
        or user.depth_cents is None
        or reference.depth_cents is None
    ):
        # Both flagged `detected`, so rate/depth should always be set; treat
        # this as "no match" rather than crash the stage on a bad profile.
        return 0.0

    rate_error = abs(user.rate_hz - reference.rate_hz) / VIBRATO_RATE_TOLERANCE_HZ
    depth_error = abs(user.depth_cents - reference.depth_cents) / VIBRATO_DEPTH_TOLERANCE_CENTS
    penalty = min(1.0, 0.5 * rate_error + 0.5 * depth_error)
    return round(100.0 * (1.0 - penalty), 1)


class VibratoStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `score` (0-100), `user` and `reference` profiles
    (`detected`, `rate_hz`, `depth_cents`).
    """

    name = STAGE_NAME
    timeout_seconds = VIBRATO_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        pitch_data = context.result("pitch").data
        user_curve = PitchCurve.model_validate(pitch_data["user_pitch_curve"])

        user_profile = _profile(user_curve)
        reference_profile = _profile(context.reference_pitch)
        score = _score(user_profile, reference_profile)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "score": score,
                "user": user_profile.__dict__,
                "reference": reference_profile.__dict__,
            },
        )
