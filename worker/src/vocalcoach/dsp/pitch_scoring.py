"""Shared pitch-vs-reference comparison logic (spec 6.3.5): scoring a user
F0 curve against the reference through the align stage's time map. Used by
both `PitchStage` (A5, `clean`, pyworld/CREPE/pYIN on the raw recording) and
`MelodyPitchStage` (A4, `mixed`, melody extraction on the mixture) --
identical math regardless of which engine produced the curve (spec 12.1 DRY:
this was duplicated once already, between align and timbre's MFCC calls,
before the shared feature cache existed; it does not get duplicated again
for a second pitch source).
"""

from __future__ import annotations

import math

from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import PITCH_SCORE_CENTS_FOR_ZERO


def voiced_fraction(hz: list[float | None]) -> float:
    if not hz:
        return 0.0
    return sum(1 for value in hz if value is not None) / len(hz)


def cents_deviation(user_hz: float, reference_hz: float) -> float:
    return 1200.0 * math.log2(user_hz / reference_hz)


def score_from_mean_abs_cents(mean_abs_cents: float) -> float:
    fraction = min(1.0, mean_abs_cents / PITCH_SCORE_CENTS_FOR_ZERO)
    return round(100.0 * (1.0 - fraction), 1)


def align_and_compare(
    user_hz: list[float | None],
    reference_hz: list[float | None],
    time_map: TimeMap,
    hop_seconds: float,
) -> tuple[list[float | None], list[float | None]]:
    """Walks `user_hz` (at its own `hop_seconds`) and looks up each frame's
    counterpart in `reference_hz` through `time_map` -- the align stage's
    warping path runs on a coarser hop, so frame indices are never compared
    directly, only the times they represent.

    Returns, per user frame: the reference Hz value at that corresponding
    time (`None` if out of range or unvoiced there) and the signed cents
    deviation (`None` if either side is unvoiced/out of range). Both lists
    are the same length as `user_hz` -- this is the single place both the
    piano-roll overlay and the pitch score come from.
    """
    aligned_reference: list[float | None] = []
    deviations: list[float | None] = []
    for user_index, user_value in enumerate(user_hz):
        reference_time = time_map.user_to_reference(user_index * hop_seconds)
        reference_index = round(reference_time / hop_seconds)
        reference_value = (
            reference_hz[reference_index] if 0 <= reference_index < len(reference_hz) else None
        )
        aligned_reference.append(reference_value)
        if user_value is None or reference_value is None:
            deviations.append(None)
        else:
            deviations.append(cents_deviation(user_value, reference_value))
    return aligned_reference, deviations
