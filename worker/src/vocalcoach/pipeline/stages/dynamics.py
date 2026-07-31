"""Stage 9: compare loudness/dynamics envelope shape between the recording
and the reference vocal stem, after DTW alignment (spec 6.3.8) -- did a
crescendo happen where one should have, or did the user stay flat?
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import DYNAMICS_TIMEOUT_SECONDS, FEATURES_HOP_SECONDS
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "dynamics"


def _correlation(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


class DynamicsStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `score` (0-100), `correlation` (-1..1, Pearson
    correlation between the two loudness contours; negative correlation
    scores 0 rather than going negative).
    """

    name = STAGE_NAME
    timeout_seconds = DYNAMICS_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        features_path = Path(context.result("features").data["features_path"])
        features = load_shared_features(features_path)
        user_rms, reference_rms = features.user.rms_envelope, features.reference.rms_envelope
        time_map = TimeMap.from_align_stage_data(context.result("align").data)

        aligned_reference = time_map.resample_reference_onto_user_grid(
            user_rms, reference_rms, FEATURES_HOP_SECONDS
        )
        correlation = _correlation(user_rms, aligned_reference)
        score = round(100.0 * max(0.0, correlation), 1)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"score": score, "correlation": correlation},
        )
