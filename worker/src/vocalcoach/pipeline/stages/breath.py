"""Stage 12: detect pauses (breaths/phrase boundaries) from the loudness
envelope and compare their placement between the recording and the
reference vocal stem, after DTW alignment (spec 6.3.10).
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import (
    BREATH_MIN_PAUSE_SECONDS,
    BREATH_PAUSE_MATCH_TOLERANCE_SECONDS,
    BREATH_SILENCE_RELATIVE_DB,
    BREATH_TIMEOUT_SECONDS,
    FEATURES_HOP_SECONDS,
)
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "breath"


def _pause_regions(rms: np.ndarray, hop_seconds: float) -> list[tuple[float, float]]:
    """Contiguous runs at least `BREATH_MIN_PAUSE_SECONDS` long, quieter
    than `BREATH_SILENCE_RELATIVE_DB` relative to this track's own peak.
    """
    peak = float(np.max(rms)) if len(rms) else 0.0
    if peak <= 0:
        return []
    with np.errstate(divide="ignore"):
        relative_db = 20.0 * np.log10(rms / peak)
    is_silent = relative_db < BREATH_SILENCE_RELATIVE_DB
    min_frames = max(1, round(BREATH_MIN_PAUSE_SECONDS / hop_seconds))

    regions: list[tuple[float, float]] = []
    run_start: int | None = None
    for i, silent in enumerate(is_silent):
        if silent and run_start is None:
            run_start = i
        elif not silent and run_start is not None:
            if i - run_start >= min_frames:
                regions.append((run_start * hop_seconds, i * hop_seconds))
            run_start = None
    if run_start is not None and len(is_silent) - run_start >= min_frames:
        regions.append((run_start * hop_seconds, len(is_silent) * hop_seconds))
    return regions


def _score(
    user_regions: list[tuple[float, float]],
    reference_regions: list[tuple[float, float]],
    time_map: TimeMap,
) -> tuple[float, int]:
    if not reference_regions:
        return 100.0, 0

    user_centers = [(start + end) / 2 for start, end in user_regions]
    matched = 0
    for start, end in reference_regions:
        expected_user_time = time_map.reference_to_user((start + end) / 2)
        if any(
            abs(center - expected_user_time) <= BREATH_PAUSE_MATCH_TOLERANCE_SECONDS
            for center in user_centers
        ):
            matched += 1
    return round(100.0 * matched / len(reference_regions), 1), matched


class BreathStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `score` (0-100), `matched_pauses`,
    `reference_pause_count`, `user_pause_count`.
    """

    name = STAGE_NAME
    timeout_seconds = BREATH_TIMEOUT_SECONDS
    #: spec 6.5/6.6: `mixed` has accompaniment sounding through pauses
    #: between phrases, so the pause signal itself disappears -- never
    #: scored there at all, not merely unreliable.
    modes = frozenset({"clean"})

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        features_path = Path(context.result("features").data["features_path"])
        features = load_shared_features(features_path)
        time_map = TimeMap.from_align_stage_data(context.result("align").data)

        user_regions = _pause_regions(features.user.rms_envelope, FEATURES_HOP_SECONDS)
        reference_regions = _pause_regions(features.reference.rms_envelope, FEATURES_HOP_SECONDS)
        score, matched = _score(user_regions, reference_regions, time_map)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "score": score,
                "matched_pauses": matched,
                "reference_pause_count": len(reference_regions),
                "user_pause_count": len(user_regions),
            },
        )
