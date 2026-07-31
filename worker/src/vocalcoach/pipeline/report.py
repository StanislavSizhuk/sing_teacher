"""Stage 11's text report (spec 6.3.11, FR-32): a short overall summary plus
one concrete, numbers-grounded paragraph per aspect, built from the same
stage data stages 5-10 already computed -- no DSP, no I/O, no recomputed
scoring logic, just prose over numbers that already exist.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from vocalcoach.constants import (
    FEEDBACK_EXCELLENT_THRESHOLD,
    FEEDBACK_FAIR_THRESHOLD,
    FEEDBACK_GOOD_THRESHOLD,
    VIBRATO_DEPTH_TOLERANCE_CENTS,
    VIBRATO_RATE_TOLERANCE_HZ,
)
from vocalcoach.models.results import StageResult

_ASPECT_LABELS: dict[str, str] = {
    "pitch": "Pitch accuracy",
    "rhythm": "Rhythm and timing",
    "breath": "Breath and phrasing",
    "dynamics": "Dynamics",
    "vibrato": "Vibrato",
    "timbre": "Timbre",
}

# Spec 6.3.9 makes this disclaimer mandatory in the user-facing report
# itself, not just in code comments: timbre is a rough similarity signal,
# never a diagnosis of vocal technique.
_TIMBRE_DISCLAIMER = (
    "This is a rough indicator of how similar your tone color sounds to the "
    "reference, not a diagnosis of your vocal technique."
)

# Spec 6.16: shown when A3 flags likely accompaniment in a `clean`-declared
# recording -- a warning, never a blocker or a score penalty (`clean`'s a
# cappella assumption, spec 2.3, is a product recommendation, not something
# the pipeline enforces by failing the analysis).
_ACCOMPANIMENT_IN_CLEAN_WARNING = (
    "Heads up: parts of your recording have energy that doesn't look like a "
    "clean solo voice. This app assumes `clean` recordings are a cappella, "
    "without background music or instruments picked up by the mic -- if "
    "that wasn't the case here, treat the scores above as less precise than "
    "usual, or retry this analysis in `mixed` mode."
)

# FR-41/6.19: unavailable aspects get their own block with the reason, never
# a silently missing section.
_UNAVAILABLE_REASON_TEXT: dict[str, str] = {
    "NOT_MEASURABLE_WITH_ACCOMPANIMENT": (
        "not measurable with accompaniment present in this recording"
    ),
}

_TIER_EXCELLENT = "excellent"
_TIER_GOOD = "good"
_TIER_FAIR = "fair"
_TIER_POOR = "poor"


def _tier(score: float) -> str:
    if score >= FEEDBACK_EXCELLENT_THRESHOLD:
        return _TIER_EXCELLENT
    if score >= FEEDBACK_GOOD_THRESHOLD:
        return _TIER_GOOD
    if score >= FEEDBACK_FAIR_THRESHOLD:
        return _TIER_FAIR
    return _TIER_POOR


def _pitch_feedback(data: dict[str, Any]) -> str:
    cents = data["mean_abs_cents"]
    tier = _tier(data["score"])
    if tier == _TIER_EXCELLENT:
        return (
            f"Right on target: your pitch tracked the reference within {cents:.0f} cents "
            "on average."
        )
    if tier == _TIER_GOOD:
        return (
            f"Mostly accurate, {cents:.0f} cents off the reference on average. "
            "Listen back and isolate the handful of notes that drifted."
        )
    if tier == _TIER_FAIR:
        return (
            f"Noticeably off pitch, {cents:.0f} cents off the reference on average. "
            "Practice the melody slowly against a piano or tuner before singing it at tempo."
        )
    return (
        f"Pitch drifted by {cents:.0f} cents on average -- more than a semitone in places. "
        "Work on matching single sustained notes to a reference tone before the full phrase."
    )


def _rhythm_feedback(data: dict[str, Any]) -> str:
    offset_ms = data["mean_abs_offset_ms"]
    tier = _tier(data["score"])
    within = data["onsets_within_tolerance"]
    total = data["reference_onset_count"]
    if tier == _TIER_EXCELLENT:
        return f"Timing was tight: {within}/{total} notes landed within the tolerance window."
    if tier == _TIER_GOOD:
        return (
            f"Timing was mostly solid, {offset_ms:.0f} ms off the reference on average "
            f"({within}/{total} notes within tolerance). A metronome on the trickier lines "
            "will help."
        )
    if tier == _TIER_FAIR:
        return (
            f"Entries were often early or late, {offset_ms:.0f} ms off the reference on average. "
            "Practice singing along with the reference track to internalize the exact timing."
        )
    return (
        f"Timing diverged a lot from the reference, {offset_ms:.0f} ms off on average. "
        "Start by clapping or tapping the rhythm alone before adding the melody back in."
    )


def _breath_feedback(data: dict[str, Any]) -> str:
    matched = data["matched_pauses"]
    total = data["reference_pause_count"]
    if total == 0:
        return "The reference has no clear breath points to compare against; nothing to flag here."
    tier = _tier(data["score"])
    if tier == _TIER_EXCELLENT:
        return f"Breaths landed where the reference takes them: {matched}/{total} matched."
    if tier in (_TIER_GOOD, _TIER_FAIR):
        return (
            f"Breathing was partly in the right places ({matched}/{total} matched). "
            "Mark the reference's breath points in your lyrics sheet for the rest."
        )
    return (
        f"Breaths often didn't line up with the reference ({matched}/{total} matched). "
        "Plan where to breathe ahead of time, at the same phrase boundaries as the reference, "
        "and practice sustaining a full phrase on one breath."
    )


def _dynamics_feedback(data: dict[str, Any]) -> str:
    correlation = data["correlation"]
    tier = _tier(data["score"])
    if tier == _TIER_EXCELLENT:
        return "Your loudness contour closely followed the reference's crescendos and dips."
    if tier in (_TIER_GOOD, _TIER_FAIR):
        return (
            f"Dynamics partly tracked the reference (correlation {correlation:.2f}). "
            "Push a little harder into the loud passages and pull back further into the quiet ones."
        )
    return (
        f"Your volume stayed fairly flat next to the reference (correlation {correlation:.2f}). "
        "Exaggerate the crescendos and diminuendos more than feels natural at first -- "
        "it usually still reads as normal from the listener's side."
    )


def _vibrato_rate_depth_note(user: dict[str, Any], reference: dict[str, Any]) -> str:
    rate_diff = user["rate_hz"] - reference["rate_hz"]
    depth_diff = user["depth_cents"] - reference["depth_cents"]
    details: list[str] = []
    if abs(rate_diff) > VIBRATO_RATE_TOLERANCE_HZ:
        details.append("faster" if rate_diff > 0 else "slower")
        details[-1] += " in rate"
    if abs(depth_diff) > VIBRATO_DEPTH_TOLERANCE_CENTS:
        details.append(("wider" if depth_diff > 0 else "narrower") + " in depth")
    return " and ".join(details) if details else "a bit different in character"


def _vibrato_feedback(data: dict[str, Any]) -> str:
    user, reference = data["user"], data["reference"]
    if not reference["detected"]:
        if user["detected"]:
            return (
                "You added vibrato where the reference sings a straight tone. "
                "Try holding a few sustained notes perfectly steady for practice."
            )
        return "Neither you nor the reference use vibrato here -- nothing to flag."
    if not user["detected"]:
        return (
            "The reference uses vibrato on sustained notes that you sang straight. "
            "Vibrato usually comes from relaxed, breath-supported sustain -- "
            "practice holding a note and letting a gentle wave develop naturally."
        )
    if _tier(data["score"]) in (_TIER_EXCELLENT, _TIER_GOOD):
        return "Your vibrato's rate and depth were a close match to the reference's."
    note = _vibrato_rate_depth_note(user, reference)
    return f"Vibrato was present but {note} than the reference's."


def _timbre_feedback(data: dict[str, Any]) -> str:
    if _tier(data["score"]) in (_TIER_EXCELLENT, _TIER_GOOD):
        return f"Your tone color was close to the reference's. {_TIMBRE_DISCLAIMER}"
    return (
        f"Your tone color differed noticeably from the reference's. {_TIMBRE_DISCLAIMER} "
        'A different natural voice is not something to "fix".'
    )


_ASPECT_FEEDBACK: dict[str, Callable[[dict[str, Any]], str]] = {
    "pitch": _pitch_feedback,
    "rhythm": _rhythm_feedback,
    "breath": _breath_feedback,
    "dynamics": _dynamics_feedback,
    "vibrato": _vibrato_feedback,
    "timbre": _timbre_feedback,
}


def _overall_summary(aspect_scores: dict[str, float], overall_score: float) -> str:
    focus_aspect = min(aspect_scores, key=lambda aspect: aspect_scores[aspect])
    return (
        f"Overall score: {overall_score:.0f}/100. "
        f"The biggest opportunity to improve is {_ASPECT_LABELS[focus_aspect].lower()} "
        f"({aspect_scores[focus_aspect]:.0f}/100)."
    )


def build_feedback_report(
    aspect_results: dict[str, StageResult],
    overall_score: float,
    *,
    aspects: Sequence[str],
    unavailable_aspects: dict[str, str],
    background_music_warning: bool = False,
) -> str:
    """Builds the FR-32 text report: one summary line, an optional spec 6.16
    warning, then one paragraph per *available* aspect (spec 6.14's
    per-mode set, in spec 6.4's canonical order), each grounded in that
    aspect's own stage data rather than generic advice, and finally one
    block per unavailable aspect explaining why (spec 6.19 -- never just
    silently missing). `aspect_results` has one entry per `aspects`, each
    carrying a `"score"` key.
    """
    aspect_scores = {
        aspect: float(result.data["score"]) for aspect, result in aspect_results.items()
    }
    sections = [_overall_summary(aspect_scores, overall_score)]
    if background_music_warning:
        sections.append(_ACCOMPANIMENT_IN_CLEAN_WARNING)
    for aspect in aspects:
        result = aspect_results[aspect]
        label = _ASPECT_LABELS[aspect]
        body: str = _ASPECT_FEEDBACK[aspect](result.data)
        sections.append(f"{label} ({aspect_scores[aspect]:.0f}/100): {body}")
    for aspect, reason in unavailable_aspects.items():
        label = _ASPECT_LABELS[aspect]
        reason_text = _UNAVAILABLE_REASON_TEXT.get(reason, reason)
        sections.append(f"{label}: not scored this time -- {reason_text}.")
    return "\n\n".join(sections)
