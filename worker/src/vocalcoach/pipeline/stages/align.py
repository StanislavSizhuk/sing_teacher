"""Stage 5: DTW-align the user's recording to the reference vocal stem
(spec 6.3.4, ADR-0004, 6.7). Every later stage compares the two signals
through the mapping this stage produces, since the user did not sing at
exactly the reference's tempo.

Two levels (spec 6.7): a coarse pass over the shared feature cache's MFCC
(one frame every `FEATURES_HOP_SECONDS`) finds the overall correspondence
within a wide-but-bounded band; a second pass refines it at a much finer
hop (`PITCH_HOP_SECONDS`), in a narrow band centered on the coarse path
instead of the diagonal. Both passes are banded (`O(n * band)` memory, spec
NFR-16) and numba-jit (NFR-17) -- see `dsp/dtw.py`.
"""

from __future__ import annotations

import time
from pathlib import Path

from vocalcoach.constants import (
    ALIGN_MAX_NORMALIZED_DISTANCE,
    ALIGN_REFINE_WINDOW_SECONDS,
    ALIGN_TIMEOUT_SECONDS,
    ALIGN_WINDOW_SECONDS,
    FEATURES_HOP_SECONDS,
    PITCH_HOP_SECONDS,
)
from vocalcoach.dsp.dtw import banded_dtw, refine_center
from vocalcoach.dsp.features import compute_mfcc, load_shared_features
from vocalcoach.errors import AlignmentFailed
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "align"

_MAX_NORMALIZED_DISTANCE_MESSAGE = (
    "DTW normalized distance {distance:.1f} exceeds the {ceiling} ceiling -- "
    "recording and reference diverge too far in tempo/content to align reliably"
)


class AlignStage(PipelineStage):
    """`StageResult.data`: `index1`/`index2` (the warping path, as parallel
    frame-index arrays into the user/reference sequences at the fine hop),
    `hop_seconds`, `normalized_distance`, `coarse_normalized_distance`
    (level 1's own cost, kept for observability).
    """

    name = STAGE_NAME
    timeout_seconds = ALIGN_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        features_path = Path(context.result("features").data["features_path"])
        features = load_shared_features(features_path)

        coarse_band = max(1, round(ALIGN_WINDOW_SECONDS / FEATURES_HOP_SECONDS))
        try:
            coarse = banded_dtw(features.user.mfcc, features.reference.mfcc, coarse_band)
        except AlignmentFailed as exc:
            raise AlignmentFailed(
                f"level-1 DTW found no warping path within the {ALIGN_WINDOW_SECONDS}s "
                f"band -- recording and reference diverge too far in tempo/content "
                f"to align: {exc}"
            ) from exc

        user_fine_mfcc = compute_mfcc(Path(preprocess["recording_path"]), PITCH_HOP_SECONDS)
        stem_path = Path(context.result("separate_reference").data["stem_path"])
        reference_fine_mfcc = compute_mfcc(stem_path, PITCH_HOP_SECONDS)

        refine_band = max(1, round(ALIGN_REFINE_WINDOW_SECONDS / PITCH_HOP_SECONDS))
        full_center = refine_center(
            coarse,
            FEATURES_HOP_SECONDS,
            PITCH_HOP_SECONDS,
            n_fine=user_fine_mfcc.shape[0],
            m_fine=reference_fine_mfcc.shape[0],
        )
        fine = banded_dtw(user_fine_mfcc, reference_fine_mfcc, refine_band, full_center=full_center)

        if fine.normalized_distance > ALIGN_MAX_NORMALIZED_DISTANCE:
            raise AlignmentFailed(
                _MAX_NORMALIZED_DISTANCE_MESSAGE.format(
                    distance=fine.normalized_distance, ceiling=ALIGN_MAX_NORMALIZED_DISTANCE
                )
            )

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "index1": fine.index1,
                "index2": fine.index2,
                "hop_seconds": PITCH_HOP_SECONDS,
                "normalized_distance": fine.normalized_distance,
                "coarse_normalized_distance": coarse.normalized_distance,
            },
        )
