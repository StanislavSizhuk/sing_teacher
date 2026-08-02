"""Stage 3: compute the shared feature cache (spec 6.9) for both the user's
recording and the reference vocal stem, once, before anything that would
otherwise recompute pieces of it (alignment, rhythm, dynamics, timbre,
breath, recording-condition). The reference stem itself is cold-path output
(spec 6.6, M2) already cached on the song by the time this warm-path stage
runs. The user's side reads `voice_audio_path` (ADR-0034): `preprocess`'s
raw recording in `clean`, or `separate_recording`'s isolated stem in
`mixed` -- so `align`'s own pitch extraction and this cache's `rms_fine`
always describe the exact same signal.
"""

from __future__ import annotations

import time

from vocalcoach.constants import FEATURES_TIMEOUT_SECONDS
from vocalcoach.dsp.features import compute_shared_features, save_shared_features
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.voice_source import voice_audio_path

STAGE_NAME = "features"


class FeaturesStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `features_path` -- a `.npz` file in `work_dir`
    holding both signals' cached MFCC/RMS/onset arrays (spec 7.3: dense
    per-frame arrays are never carried as JSON, only as a file path, exactly
    like stage 1's canonical WAVs).
    """

    name = STAGE_NAME
    timeout_seconds = FEATURES_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()

        user_features = compute_shared_features(voice_audio_path(context))
        reference_features = compute_shared_features(context.reference_vocal_stem_path)

        features_path = context.work_dir / "features.npz"
        save_shared_features(features_path, user=user_features, reference=reference_features)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"features_path": str(features_path)},
        )
