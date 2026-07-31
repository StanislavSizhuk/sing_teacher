"""Weight profiles (spec 6.14): which aspects a mode scores at all, and how
`overall_score` combines them. `clean_v1` and `mixed_v1` are configured in
`SCORING_WEIGHTS_CLEAN`/`SCORING_WEIGHTS_MIXED` (spec 20.5) -- this module
only holds the mode -> aspect-set mapping and the pure combination formula,
never the numbers themselves (those are config, `config.py`).
"""

from __future__ import annotations

from vocalcoach.models.mode import Mode

#: Order matches spec 6.4/6.14's own aggregation table; every stage/report
#: that iterates aspects for a mode uses this order so logs, reports and
#: `SCORING_WEIGHTS_*` env parsing all agree (spec 12.1 DRY: one source of
#: truth for aspect order, not one per module).
ASPECTS: tuple[str, ...] = ("pitch", "rhythm", "breath", "dynamics", "vibrato", "timbre")

#: Spec 6.5/6.14: `mixed` never scores breath or timbre at all -- the stages
#: that would compute them do not even run (`BreathStage`/`TimbreStage`
#: declare `modes={"clean"}`), so there is nothing to renormalize around,
#: unlike a stage that ran and merely produced no usable result.
MODE_ASPECTS: dict[Mode, tuple[str, ...]] = {
    "clean": ASPECTS,
    "mixed": ("pitch", "rhythm", "dynamics", "vibrato"),
}

#: spec 6.14: the profile name stored with the analysis (`weights_profile`)
#: so old results stay reproducible after the formula changes.
PROFILE_NAME_BY_MODE: dict[Mode, str] = {"clean": "clean_v1", "mixed": "mixed_v1"}

#: FR-41: why an aspect this mode never scores is `null`, not `0` --
#: `unavailable_aspects` in the API response (spec 8.4) reads directly off
#: this, keyed by aspect name.
UNAVAILABLE_ASPECT_REASON: dict[str, str] = {
    "breath": "NOT_MEASURABLE_WITH_ACCOMPANIMENT",
    "timbre": "NOT_MEASURABLE_WITH_ACCOMPANIMENT",
}


def unavailable_aspects_for(mode: Mode) -> dict[str, str]:
    """Every `ASPECTS` entry this mode's own profile does not score, mapped
    to its machine-readable reason (FR-41)."""
    available = set(MODE_ASPECTS[mode])
    return {
        aspect: UNAVAILABLE_ASPECT_REASON[aspect] for aspect in ASPECTS if aspect not in available
    }


def weighted_overall_score(
    aspect_scores: dict[str, float], weights: dict[str, float], mode: Mode
) -> float:
    """Spec 6.14's weighted sum, over exactly `MODE_ASPECTS[mode]` -- never
    the full six, so a caller cannot accidentally sum in an aspect this
    mode's profile was never configured to weight.
    """
    total = sum(aspect_scores[aspect] * weights[aspect] for aspect in MODE_ASPECTS[mode])
    return round(total, 1)
