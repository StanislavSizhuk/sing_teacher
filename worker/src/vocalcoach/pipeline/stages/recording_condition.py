"""Stage A3 (spec 6.16): classifies whether the user's recording actually
contains accompaniment, and reconciles that against the mode they declared.

The product assumption for `clean` is a cappella singing in headphones
(spec 2.3); for `mixed`, any accompaniment at all. Demucs never runs on the
user's own recording either way (ADR-0003, spec 2.3's cost argument), so
this is a cheap stand-in for real source separation, not source separation
itself -- reusing the "pitch" stage's own voiced/unvoiced classification
(spec 12.1 DRY: a second per-frame voice-activity pass over the same audio
would just recompute what stage 4 already determined) plus a fresh RMS
comparison:

    accompaniment_level = median(RMS of unvoiced frames) / median(RMS of
    voiced frames)

A true solo take is close to silent in its unvoiced frames; an instrument
or backing track keeps ringing through them. This never fails the
analysis -- spec 6.16 is explicit that classification only ever adds a
warning and, for `mixed`, a cheaper/more accurate downgrade; it does not
block the result either way.

Reads `context.result("pitch")` regardless of which mode actually ran
(`PitchStage`/`MelodyPitchStage` both write there, spec 12.3), so this stage
itself runs identically in both.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from vocalcoach.constants import (
    RECORDING_CONDITION_MIN_UNVOICED_FRAMES,
    RECORDING_CONDITION_TIMEOUT_SECONDS,
)
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.mode import Mode
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "recording_condition"


def _accompaniment_level(rms: np.ndarray, voiced: np.ndarray) -> float:
    """`rms` and `voiced` are framed independently (the shared feature
    cache's `rms_fine` vs. the pitch/melody stage's own hop) -- close enough
    to compare index-for-index up to the shorter length, same tolerance the
    v1 heuristic this replaces already relied on.
    """
    frame_count = min(len(rms), len(voiced))
    if frame_count == 0:
        return 0.0
    voiced_rms = rms[:frame_count][voiced[:frame_count]]
    unvoiced_rms = rms[:frame_count][~voiced[:frame_count]]
    if len(voiced_rms) == 0 or len(unvoiced_rms) < RECORDING_CONDITION_MIN_UNVOICED_FRAMES:
        return 0.0
    voiced_median = float(np.median(voiced_rms))
    if voiced_median <= 0:
        return 0.0
    unvoiced_median = float(np.median(unvoiced_rms))
    return unvoiced_median / voiced_median


def _reconcile_mode(declared: Mode, accompaniment_detected: bool) -> tuple[Mode, list[str]]:
    """Spec 6.16's table. `effective_mode` is a diagnostic/confidence signal
    (spec 6.15) reported alongside the result of *this* run -- it does not
    retroactively change which stages already ran (see
    `docs/adr/0026-mode-reconciliation-is-diagnostic-only.md`).
    """
    if declared == "clean" and accompaniment_detected:
        return "clean", ["ACCOMPANIMENT_IN_CLEAN_MODE"]
    if declared == "mixed" and not accompaniment_detected:
        return "clean", ["MODE_DOWNGRADED_TO_CLEAN"]
    return declared, []


class RecordingConditionStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `accompaniment_level` (the ratio above),
    `accompaniment_detected` (bool, thresholded), `effective_mode`
    (spec 6.16 diagnostic, see module docstring), `warnings` (machine-
    readable codes from spec 6.18's table this stage can add).
    """

    name = STAGE_NAME
    timeout_seconds = RECORDING_CONDITION_TIMEOUT_SECONDS

    def __init__(self, accompaniment_detect_threshold: float) -> None:
        self._threshold = accompaniment_detect_threshold

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        features_path = Path(context.result("features").data["features_path"])
        features = load_shared_features(features_path)
        user_hz: list[float | None] = context.result("pitch").data["user_pitch_curve"]["hz"]
        voiced = np.array([value is not None for value in user_hz], dtype=bool)

        accompaniment_level = _accompaniment_level(features.user.rms_fine, voiced)
        detected = accompaniment_level >= self._threshold
        effective_mode, warnings = _reconcile_mode(context.mode, detected)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "accompaniment_level": round(accompaniment_level, 4),
                "accompaniment_detected": detected,
                "effective_mode": effective_mode,
                "warnings": warnings,
            },
        )
