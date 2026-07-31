"""Stage 7: compare onset timing between the recording and the reference
vocal stem, after DTW alignment (spec 6.3.6): how early or late the user
comes in on each note/syllable.
"""

from __future__ import annotations

import time
from pathlib import Path

from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import RHYTHM_ONSET_TOLERANCE_MS, RHYTHM_TIMEOUT_SECONDS
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "rhythm"


def _score_from_mean_abs_offset_ms(mean_abs_offset_ms: float) -> float:
    fraction = min(1.0, mean_abs_offset_ms / RHYTHM_ONSET_TOLERANCE_MS)
    return round(100.0 * (1.0 - fraction), 1)


class RhythmStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `score` (0-100), `mean_abs_offset_ms` (over every
    reference onset paired with its nearest user onset -- an unpaired-in-
    tolerance onset still contributes its full offset, it is not dropped
    from the average), `onsets_within_tolerance` (offset <=
    `RHYTHM_ONSET_TOLERANCE_MS`), `reference_onset_count`, `user_onset_count`.
    """

    name = STAGE_NAME
    timeout_seconds = RHYTHM_TIMEOUT_SECONDS

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        features_path = Path(context.result("features").data["features_path"])
        features = load_shared_features(features_path)
        user_onsets = [float(t) for t in features.user.onset_times]
        reference_onsets = [float(t) for t in features.reference.onset_times]
        time_map = TimeMap.from_align_stage_data(context.result("align").data)

        offsets_ms: list[float] = []
        for reference_time in reference_onsets:
            expected_user_time = time_map.reference_to_user(reference_time)
            nearest = min(
                user_onsets, key=lambda candidate: abs(candidate - expected_user_time), default=None
            )
            if nearest is None:
                continue
            offsets_ms.append(abs(nearest - expected_user_time) * 1000.0)

        mean_abs_offset_ms = sum(offsets_ms) / len(offsets_ms) if offsets_ms else 0.0
        score = _score_from_mean_abs_offset_ms(mean_abs_offset_ms) if offsets_ms else 0.0
        within_tolerance = sum(1 for offset in offsets_ms if offset <= RHYTHM_ONSET_TOLERANCE_MS)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "score": score,
                "mean_abs_offset_ms": mean_abs_offset_ms,
                "onsets_within_tolerance": within_tolerance,
                "reference_onset_count": len(reference_onsets),
                "user_onset_count": len(user_onsets),
            },
        )
