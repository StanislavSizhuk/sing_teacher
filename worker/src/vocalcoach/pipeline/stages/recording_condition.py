"""Stage 13: soft heuristic flagging likely background-music contamination
in the user's own recording (spec 2.3, 6.9).

The product assumption is a cappella singing in headphones (ADR-0003), so
the user's recording is never run through Demucs -- unlike the reference,
there is no real source separation to lean on here. This is a cheap stand-
in: a frame loud enough to matter, relative to the recording's own peak
RMS, where the pitch stage found no single clear pitch, is a signal of
non-vocal energy (instruments, background noise) rather than a pause in
singing. Never fails the analysis -- spec 6.9 is explicit that this only
adds a report warning, it does not block the result.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from vocalcoach.constants import (
    RECORDING_CONDITION_LOUD_RELATIVE_DB,
    RECORDING_CONDITION_NON_VOCAL_ENERGY_FRACTION,
    RECORDING_CONDITION_TIMEOUT_SECONDS,
)
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "recording_condition"


def _loud_unvoiced_fraction(rms: np.ndarray, user_hz: list[float | None]) -> float:
    """Fraction of frames that are both loud (relative to this recording's
    own peak) and unvoiced. Natural pauses/consonants are mostly quiet, so
    this stays low for a clean solo vocal even though every recording has
    some unvoiced frames.

    `rms` and `user_hz` are framed independently (the shared feature cache's
    `rms_fine` vs. the pitch detector), each at `PITCH_HOP_SECONDS` but not
    necessarily with identical centering -- close enough for an aggregate
    fraction over the whole recording, so frames are compared index-for-index
    up to the shorter length rather than resampled to match exactly.
    """
    frame_count = min(len(rms), len(user_hz))
    if frame_count == 0:
        return 0.0
    peak = float(np.max(rms[:frame_count]))
    if peak <= 0:
        return 0.0
    with np.errstate(divide="ignore"):
        relative_db = 20.0 * np.log10(rms[:frame_count] / peak)
    loud_and_unvoiced = sum(
        1
        for i in range(frame_count)
        if relative_db[i] >= RECORDING_CONDITION_LOUD_RELATIVE_DB and user_hz[i] is None
    )
    return loud_and_unvoiced / frame_count


class RecordingConditionStage(PipelineStage):
    """`StageResult.data`: `background_music_detected` (bool),
    `non_vocal_energy_fraction` (0-1, the loud-but-unvoiced frame fraction
    the decision is based on).
    """

    name = STAGE_NAME
    timeout_seconds = RECORDING_CONDITION_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        features_path = Path(context.result("features").data["features_path"])
        features = load_shared_features(features_path)
        user_hz = context.result("pitch").data["user_pitch_curve"]["hz"]

        fraction = _loud_unvoiced_fraction(features.user.rms_fine, user_hz)
        detected = fraction >= RECORDING_CONDITION_NON_VOCAL_ENERGY_FRACTION

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "background_music_detected": detected,
                "non_vocal_energy_fraction": round(fraction, 3),
            },
        )
