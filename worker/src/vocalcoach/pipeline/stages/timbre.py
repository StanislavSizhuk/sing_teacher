"""Stage 10: compare MFCC timbre profiles between the recording and the
reference vocal stem, after DTW alignment (spec 6.3.9).

A rough "how similar does it sound" indicator, not a diagnosis of vocal
technique -- the report text stage 13 (E4) builds from this score must say
so explicitly (spec 6.3.9's mandated disclaimer).
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import FEATURES_HOP_SECONDS, TIMBRE_TIMEOUT_SECONDS
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "timbre"


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


class TimbreStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `score` (0-100), `mean_cosine_similarity`."""

    name = STAGE_NAME
    timeout_seconds = TIMBRE_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        features_path = Path(context.result("features").data["features_path"])
        features = load_shared_features(features_path)
        user_mfcc, reference_mfcc = features.user.mfcc, features.reference.mfcc
        time_map = TimeMap.from_align_stage_data(context.result("align").data)

        similarities: list[float] = []
        last_index = len(reference_mfcc) - 1
        for i, user_vector in enumerate(user_mfcc):
            reference_time = time_map.user_to_reference(i * FEATURES_HOP_SECONDS)
            reference_index = min(max(round(reference_time / FEATURES_HOP_SECONDS), 0), last_index)
            similarities.append(_cosine_similarity(user_vector, reference_mfcc[reference_index]))

        mean_similarity = sum(similarities) / len(similarities) if similarities else 0.0
        score = round(100.0 * max(0.0, mean_similarity), 1)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"score": score, "mean_cosine_similarity": mean_similarity},
        )
