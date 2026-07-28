"""Stage 9: compare MFCC timbre profiles between the recording and the
reference vocal stem, after DTW alignment (spec 6.3.9).

A rough "how similar does it sound" indicator, not a diagnosis of vocal
technique -- the report text stage 11 (E4) builds from this score must say
so explicitly (spec 6.3.9's mandated disclaimer).
"""

from __future__ import annotations

import time
from pathlib import Path

import librosa
import numpy as np

from vocalcoach.audio.io import read_mono
from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import (
    ENVELOPE_HOP_SECONDS,
    TIMBRE_MFCC_COEFFICIENTS,
    TIMBRE_TIMEOUT_SECONDS,
)
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "timbre"


def _mfcc(path: Path, hop_seconds: float) -> np.ndarray:
    samples, sample_rate = read_mono(path)
    hop_length = max(1, round(sample_rate * hop_seconds))
    mfcc = librosa.feature.mfcc(
        y=samples, sr=sample_rate, n_mfcc=TIMBRE_MFCC_COEFFICIENTS, hop_length=hop_length
    )
    return np.asarray(mfcc.T)  # (n_frames, n_mfcc)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


class TimbreStage(PipelineStage):
    """`StageResult.data`: `score` (0-100), `mean_cosine_similarity`."""

    name = STAGE_NAME
    timeout_seconds = TIMBRE_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        user_mfcc = _mfcc(Path(preprocess["recording_path"]), ENVELOPE_HOP_SECONDS)
        reference_mfcc = _mfcc(
            Path(context.result("separate_reference").data["stem_path"]), ENVELOPE_HOP_SECONDS
        )
        time_map = TimeMap.from_align_stage_data(context.result("align").data)

        similarities: list[float] = []
        last_index = len(reference_mfcc) - 1
        for i, user_vector in enumerate(user_mfcc):
            reference_time = time_map.user_to_reference(i * ENVELOPE_HOP_SECONDS)
            reference_index = min(max(round(reference_time / ENVELOPE_HOP_SECONDS), 0), last_index)
            similarities.append(_cosine_similarity(user_vector, reference_mfcc[reference_index]))

        mean_similarity = sum(similarities) / len(similarities) if similarities else 0.0
        score = round(100.0 * max(0.0, mean_similarity), 1)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"score": score, "mean_cosine_similarity": mean_similarity},
        )
