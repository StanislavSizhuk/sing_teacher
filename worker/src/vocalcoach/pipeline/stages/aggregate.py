"""Stage 12: weighted-sum the six aspect scores into `overall_score` and
build the FR-32 text report (spec 6.2 row 11, 6.3.11, 6.4). Pure arithmetic
and string-building over stage results stages 5-11 already computed -- no
DSP, no I/O beyond returning its own `StageResult`.
"""

from __future__ import annotations

import time

from vocalcoach.config import ASPECTS, ScoringWeights
from vocalcoach.constants import AGGREGATE_TIMEOUT_SECONDS
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.report import build_feedback_report

STAGE_NAME = "aggregate"


def _weighted_overall_score(aspect_scores: dict[str, float], weights: ScoringWeights) -> float:
    weight_by_aspect = weights.as_dict()
    total = sum(aspect_scores[aspect] * weight_by_aspect[aspect] for aspect in ASPECTS)
    return round(total, 1)


class AggregateStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `overall_score` (0-100, spec 6.4's weighted sum),
    `feedback_text` (FR-32), `scoring_version`, `aspect_scores` (the six
    inputs, for observability -- the per-aspect columns are already written
    by their own stages; this is not a second source of truth for them).
    """

    name = STAGE_NAME
    timeout_seconds = AGGREGATE_TIMEOUT_SECONDS

    def __init__(self, weights: ScoringWeights, scoring_version: str) -> None:
        self._weights = weights
        self._scoring_version = scoring_version

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        aspect_results = {aspect: context.result(aspect) for aspect in ASPECTS}
        aspect_scores = {
            aspect: float(result.data["score"]) for aspect, result in aspect_results.items()
        }

        overall_score = _weighted_overall_score(aspect_scores, self._weights)
        # spec 6.9: a non-blocking report warning, never a lower score --
        # the recording-condition check runs regardless of what it finds.
        background_music_detected = bool(
            context.result("recording_condition").data["background_music_detected"]
        )
        feedback_text = build_feedback_report(
            aspect_results, overall_score, background_music_detected
        )

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "overall_score": overall_score,
                "feedback_text": feedback_text,
                "scoring_version": self._scoring_version,
                "aspect_scores": aspect_scores,
            },
        )
