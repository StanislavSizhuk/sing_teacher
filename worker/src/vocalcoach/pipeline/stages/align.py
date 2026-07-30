"""Stage 5: DTW-align the user's recording to the reference vocal stem
(spec 6.3.4, ADR-0004). Every later stage compares the two signals through
the mapping this stage produces, since the user did not sing at exactly the
reference's tempo.
"""

from __future__ import annotations

import time
from pathlib import Path

from dtw import dtw

from vocalcoach.constants import (
    ALIGN_MAX_NORMALIZED_DISTANCE,
    ALIGN_TIMEOUT_SECONDS,
    ALIGN_WINDOW_SECONDS,
    FEATURES_HOP_SECONDS,
)
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.errors import AlignmentFailed
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "align"


class AlignStage(PipelineStage):
    """`StageResult.data`: `index1`/`index2` (the warping path, as parallel
    frame-index arrays into the user/reference MFCC sequences),
    `hop_seconds`, `normalized_distance`.
    """

    name = STAGE_NAME
    timeout_seconds = ALIGN_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        features_path = Path(context.result("features").data["features_path"])
        features = load_shared_features(features_path)
        user_mfcc, reference_mfcc = features.user.mfcc, features.reference.mfcc

        window_size = max(1, round(ALIGN_WINDOW_SECONDS / FEATURES_HOP_SECONDS))
        try:
            alignment = dtw(
                user_mfcc,
                reference_mfcc,
                step_pattern="symmetric2",
                window_type="sakoechiba",
                window_args={"window_size": window_size},
                keep_internals=False,
                distance_only=False,
            )
        except ValueError as exc:
            # dtw-python raises a bare ValueError ("No warping path found
            # compatible with the local constraints"), not one of its own
            # exception types, when the two signals diverge so far in
            # length/tempo that no path exists inside the Sakoe-Chiba
            # window at all -- a property of this input, exactly like the
            # normalized-distance check below, so it must not retry
            # (spec 6.8) or surface as an opaque INTERNAL error.
            raise AlignmentFailed(
                f"DTW found no warping path within the {ALIGN_WINDOW_SECONDS}s window -- "
                f"recording and reference diverge too far in tempo/content to align: {exc}"
            ) from exc
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
                "hop_seconds": FEATURES_HOP_SECONDS,
                "normalized_distance": normalized_distance,
            },
        )
