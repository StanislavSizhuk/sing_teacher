"""Stage A10: weighted-sum the mode's own aspect scores into `overall_score`
(spec 6.14), compute the confidence model (spec 6.15), and build the FR-32
text report. Pure arithmetic and string-building over stage results earlier
stages already computed -- no DSP, no I/O beyond returning its own
`StageResult`. The actual weighting/confidence formulas live in `scoring/`
(spec 12.3); this stage's job is gathering their inputs off `context` and
handing the result to `pipeline/report.py`.
"""

from __future__ import annotations

import time

from vocalcoach.config import ScoringWeights
from vocalcoach.constants import AGGREGATE_TIMEOUT_SECONDS
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.mode import Mode
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.report import build_feedback_report
from vocalcoach.scoring.confidence import ConfidenceSignals, compute_confidence
from vocalcoach.scoring.weights import (
    MODE_ASPECTS,
    PROFILE_NAME_BY_MODE,
    unavailable_aspects_for,
    weighted_overall_score,
)

STAGE_NAME = "aggregate"


class AggregateStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `overall_score` (0-100, spec 6.14's weighted sum
    over `mode`'s own aspects), `feedback_text` (FR-32), `scoring_version`,
    `weights_profile` (`clean_v1`/`mixed_v1`), `aspect_scores` (only the
    aspects this mode scores), `unavailable_aspects` (FR-41: the rest,
    mapped to a machine-readable reason), `confidence`/`aspect_confidence`/
    `warnings` (spec 6.15), `key_shift_semitones` (spec 6.8, `None` unless
    A8 applied one).
    """

    name = STAGE_NAME
    timeout_seconds = AGGREGATE_TIMEOUT_SECONDS

    def __init__(self, weights_by_mode: dict[Mode, ScoringWeights], scoring_version: str) -> None:
        self._weights_by_mode = weights_by_mode
        self._scoring_version = scoring_version

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        mode = context.mode
        aspects = MODE_ASPECTS[mode]

        aspect_results = {aspect: context.result(aspect) for aspect in aspects}
        aspect_scores = {
            aspect: float(result.data["score"]) for aspect, result in aspect_results.items()
        }

        key_normalization = context.result("key_normalization").data
        if key_normalization["applied"] and key_normalization["adjusted_score"] is not None:
            aspect_scores["pitch"] = float(key_normalization["adjusted_score"])

        weights = self._weights_by_mode[mode].as_dict()
        overall_score = weighted_overall_score(aspect_scores, weights, mode)

        recording_condition = context.result("recording_condition").data
        accompaniment_in_clean = mode == "clean" and bool(
            recording_condition["accompaniment_detected"]
        )
        pitch_result = context.result("pitch").data
        align_result = context.result("align").data
        align_cost = float(align_result["normalized_distance"])

        confidence = compute_confidence(
            ConfidenceSignals(
                mode=mode,
                accompaniment_in_clean=accompaniment_in_clean,
                voiced_ratio=float(pitch_result["voiced_fraction"]),
                alignment_cost=align_cost,
                key_shift_out_of_range=bool(key_normalization["out_of_range"]),
                length_mismatch=bool(align_result["length_mismatch"]),
                reference_start_offset_detected=float(
                    align_result["reference_start_offset_seconds"]
                )
                > 0,
            )
        )
        warnings = [*recording_condition["warnings"], *confidence.warnings]

        unavailable_aspects = unavailable_aspects_for(mode)
        feedback_text = build_feedback_report(
            aspect_results,
            overall_score,
            aspects=aspects,
            unavailable_aspects=unavailable_aspects,
            background_music_warning=accompaniment_in_clean,
            locale=context.locale,
        )

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "overall_score": overall_score,
                "feedback_text": feedback_text,
                "scoring_version": self._scoring_version,
                "weights_profile": PROFILE_NAME_BY_MODE[mode],
                "aspect_scores": aspect_scores,
                "unavailable_aspects": unavailable_aspects,
                "effective_mode": recording_condition["effective_mode"],
                "confidence": confidence.overall,
                "aspect_confidence": confidence.aspect_confidence,
                "warnings": warnings,
                "key_shift_semitones": key_normalization["key_shift_semitones"],
                "accompaniment_level": recording_condition["accompaniment_level"],
                "voiced_ratio": pitch_result["voiced_fraction"],
                "alignment_cost": align_cost,
            },
        )
