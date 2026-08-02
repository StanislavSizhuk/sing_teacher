"""Confidence model (spec 6.15): every analysis reports an overall level and
a per-aspect level (`high`/`medium`/`low`), because a mode/measurement that
is inherently less reliable should say so rather than hand back a
precise-looking number (spec G7).

Pure function over already-computed signals -- `AggregateStage` gathers
`voiced_ratio`/`alignment_cost`/etc. off earlier stages' results and hands
them here; this module knows nothing about `PipelineContext` or `StageResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vocalcoach.constants import CONFIDENCE_LOW_VOICED_RATIO, CONFIDENCE_WEAK_ALIGNMENT_COST
from vocalcoach.models.mode import Mode
from vocalcoach.scoring.weights import MODE_ASPECTS

ConfidenceLevel = Literal["high", "medium", "low"]

#: Ordered low-to-high so "one step down" is just an index shift.
_LEVELS: tuple[ConfidenceLevel, ...] = ("low", "medium", "high")

#: Spec 6.15: a low-voiced-ratio signal on a `mixed` analysis specifically
#: undermines the two aspects melody extraction (A4) itself feeds -- pitch
#: and vibrato are read directly off its F0 curve, rhythm and dynamics are
#: not.
_MELODY_DEPENDENT_ASPECTS: tuple[str, ...] = ("pitch", "vibrato")


def _step_down(level: ConfidenceLevel, steps: int = 1) -> ConfidenceLevel:
    return _LEVELS[max(0, _LEVELS.index(level) - steps)]


@dataclass(frozen=True)
class ConfidenceSignals:
    """Everything spec 6.15's table needs, gathered from earlier stages'
    results by `AggregateStage` -- see that table for which stage produces
    each field."""

    mode: Mode
    accompaniment_in_clean: bool  # A3
    voiced_ratio: float  # A4/A5 (whichever ran)
    alignment_cost: float  # A7
    key_shift_out_of_range: bool  # A8
    length_mismatch: bool  # A7 (ADR-0030): recording/reference cropped to a shared overlap


@dataclass(frozen=True)
class ConfidenceResult:
    overall: ConfidenceLevel
    aspect_confidence: dict[str, ConfidenceLevel]
    warnings: tuple[str, ...]


def compute_confidence(signals: ConfidenceSignals) -> ConfidenceResult:
    # spec 6.15: mode mixed puts a medium ceiling on confidence before any
    # other signal is even considered.
    overall: ConfidenceLevel = "medium" if signals.mode == "mixed" else "high"
    warnings: list[str] = []

    if signals.accompaniment_in_clean:
        overall = _step_down(overall)
        warnings.append("ACCOMPANIMENT_IN_CLEAN_MODE")
    if signals.voiced_ratio < CONFIDENCE_LOW_VOICED_RATIO:
        overall = _step_down(overall)
        warnings.append("LITTLE_VOICE_DETECTED")
    if signals.alignment_cost > CONFIDENCE_WEAK_ALIGNMENT_COST:
        overall = _step_down(overall)
        warnings.append("WEAK_ALIGNMENT")
    if signals.key_shift_out_of_range:
        overall = _step_down(overall)
        warnings.append("KEY_SHIFT_OUT_OF_RANGE")
    if signals.length_mismatch:
        overall = _step_down(overall)
        warnings.append("LENGTH_MISMATCH_PARTIAL_ANALYSIS")

    aspect_confidence: dict[str, ConfidenceLevel] = dict.fromkeys(
        MODE_ASPECTS[signals.mode], overall
    )
    if signals.mode == "mixed" and signals.voiced_ratio < CONFIDENCE_LOW_VOICED_RATIO:
        for aspect in _MELODY_DEPENDENT_ASPECTS:
            if aspect in aspect_confidence:
                aspect_confidence[aspect] = _step_down(aspect_confidence[aspect])

    return ConfidenceResult(
        overall=overall, aspect_confidence=aspect_confidence, warnings=tuple(warnings)
    )
