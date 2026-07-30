"""Stage 3: compute the shared feature cache (spec 6.9) for both the user's
recording and the reference vocal stem, once, right after both signals
exist (stage 1 preprocess, stage 2 separation) and before anything that
would otherwise recompute pieces of it (alignment, rhythm, dynamics,
timbre, breath, recording-condition).
"""

from __future__ import annotations

import time
from pathlib import Path

from vocalcoach.constants import FEATURES_TIMEOUT_SECONDS
from vocalcoach.dsp.features import compute_shared_features, save_shared_features
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "features"


class FeaturesStage(PipelineStage):
    """`StageResult.data`: `features_path` -- a `.npz` file in `work_dir`
    holding both signals' cached MFCC/RMS/onset arrays (spec 7.3: dense
    per-frame arrays are never carried as JSON, only as a file path, exactly
    like stage 1's canonical WAVs).
    """

    name = STAGE_NAME
    timeout_seconds = FEATURES_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        stem_path = Path(context.result("separate_reference").data["stem_path"])

        user_features = compute_shared_features(Path(preprocess["recording_path"]))
        reference_features = compute_shared_features(stem_path)

        features_path = context.work_dir / "features.npz"
        save_shared_features(features_path, user=user_features, reference=reference_features)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"features_path": str(features_path)},
        )
