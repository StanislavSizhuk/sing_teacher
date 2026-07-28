"""Stage 4: DTW-align the user's recording to the reference vocal stem
(spec 6.3.4, ADR-0004). Every later stage compares the two signals through
the mapping this stage produces, since the user did not sing at exactly the
reference's tempo.
"""

from __future__ import annotations

import time
from pathlib import Path

import librosa
import numpy as np
from dtw import dtw

from vocalcoach.audio.io import read_mono
from vocalcoach.constants import (
    ALIGN_HOP_SECONDS,
    ALIGN_MAX_NORMALIZED_DISTANCE,
    ALIGN_MFCC_COEFFICIENTS,
    ALIGN_TIMEOUT_SECONDS,
    ALIGN_WINDOW_SECONDS,
)
from vocalcoach.errors import AlignmentFailed
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "align"


def _mfcc_frames(path: Path, hop_length: int) -> np.ndarray:
    samples, sample_rate = read_mono(path)
    mfcc = librosa.feature.mfcc(
        y=samples, sr=sample_rate, n_mfcc=ALIGN_MFCC_COEFFICIENTS, hop_length=hop_length
    )
    return np.asarray(mfcc.T)  # (n_frames, n_mfcc), one row per time step


class AlignStage(PipelineStage):
    """`StageResult.data`: `index1`/`index2` (the warping path, as parallel
    frame-index arrays into the user/reference MFCC sequences),
    `hop_seconds`, `normalized_distance`.
    """

    name = STAGE_NAME
    timeout_seconds = ALIGN_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        sample_rate = int(preprocess["sample_rate_hz"])
        hop_length = max(1, round(sample_rate * ALIGN_HOP_SECONDS))

        user_mfcc = _mfcc_frames(Path(preprocess["recording_path"]), hop_length)
        reference_mfcc = _mfcc_frames(
            Path(context.result("separate_reference").data["stem_path"]), hop_length
        )

        window_size = max(1, round(ALIGN_WINDOW_SECONDS / ALIGN_HOP_SECONDS))
        alignment = dtw(
            user_mfcc,
            reference_mfcc,
            step_pattern="symmetric2",
            window_type="sakoechiba",
            window_args={"window_size": window_size},
            keep_internals=False,
            distance_only=False,
        )
        normalized_distance = float(alignment.normalizedDistance)

        if normalized_distance > ALIGN_MAX_NORMALIZED_DISTANCE:
            raise AlignmentFailed(
                f"DTW normalized distance {normalized_distance:.1f} exceeds the "
                f"{ALIGN_MAX_NORMALIZED_DISTANCE} ceiling -- recording and reference "
                "diverge too far in tempo/content to align reliably"
            )

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "index1": [int(i) for i in alignment.index1],
                "index2": [int(i) for i in alignment.index2],
                "hop_seconds": ALIGN_HOP_SECONDS,
                "normalized_distance": normalized_distance,
            },
        )
