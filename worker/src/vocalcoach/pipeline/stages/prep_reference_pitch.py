"""Stage P4: track the reference vocal stem's pitch curve (spec 6.4,
6.3.5), the last cold-path stage. Runs exactly once per song, with the same
VAD-gated detector every warm-path analysis of this song's `PitchStage`
uses for the user's own recording (spec 6.6: determinism requires the same
engine on both sides of a comparison) -- `detect_gated` is shared between
the two rather than reimplemented (spec 12.1 DRY).
"""

from __future__ import annotations

import time

from vocalcoach.audio.io import read_mono
from vocalcoach.constants import PITCH_HOP_SECONDS, PREP_REFERENCE_PITCH_TIMEOUT_SECONDS
from vocalcoach.dsp.features import compute_shared_features
from vocalcoach.dsp.pitch_detection import detect_gated
from vocalcoach.models.audio import PitchCurve
from vocalcoach.models.context import SongPrepContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import PitchDetector

STAGE_NAME = "prep_reference_pitch"


class PrepReferencePitchStage(PipelineStage[SongPrepContext]):
    """`StageResult.data`: `reference_pitch_curve` (a JSON-encoded
    `PitchCurve`), cached on the song forever once written (spec 6.6, 7.2).
    """

    name = STAGE_NAME
    timeout_seconds = PREP_REFERENCE_PITCH_TIMEOUT_SECONDS

    def __init__(self, detector: PitchDetector) -> None:
        self._detector = detector

    def run(self, context: SongPrepContext) -> StageResult:
        start = time.monotonic()

        features = compute_shared_features(context.vocal_stem_path)
        try:
            reference_samples, sample_rate = read_mono(context.vocal_stem_path)
            reference_hz = detect_gated(
                self._detector, reference_samples, sample_rate, PITCH_HOP_SECONDS, features.rms_fine
            )
        finally:
            self._detector.release()

        reference_curve = PitchCurve(hop_seconds=PITCH_HOP_SECONDS, hz=reference_hz)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"reference_pitch_curve": reference_curve.model_dump(mode="json")},
        )
