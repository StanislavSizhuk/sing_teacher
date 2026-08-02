"""Stage 11's text report (spec 6.3.11, FR-32): a short overall summary plus
one concrete, numbers-grounded paragraph per aspect, built from the same
stage data stages 5-10 already computed -- no DSP, no I/O, no recomputed
scoring logic, just prose over numbers that already exist.

ADR-0031: which outcome applies (tier, matched-pause count, which of
vibrato's rate/depth combinations fits) is decided exactly once, regardless
of locale -- only the final step, turning that outcome into a sentence, is
a per-locale template lookup. Adding a language means adding a dict entry
here, never touching the decision logic above it.
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
from vocalcoach.models.locale import DEFAULT_LOCALE, Locale
from vocalcoach.models.results import StageResult

_ASPECT_LABELS: dict[Locale, dict[str, str]] = {
    "en": {
        "pitch": "Pitch accuracy",
        "rhythm": "Rhythm and timing",
        "breath": "Breath and phrasing",
        "dynamics": "Dynamics",
        "vibrato": "Vibrato",
        "timbre": "Timbre",
    },
    "uk": {
        "pitch": "Точність висоти тону",
        "rhythm": "Ритм і темп",
        "breath": "Дихання і фразування",
        "dynamics": "Динаміка",
        "vibrato": "Вібрато",
        "timbre": "Тембр",
    },
}

# Spec 6.3.9 makes this disclaimer mandatory in the user-facing report
# itself, not just in code comments: timbre is a rough similarity signal,
# never a diagnosis of vocal technique.
_TIMBRE_DISCLAIMER: dict[Locale, str] = {
    "en": (
        "This is a rough indicator of how similar your tone color sounds to the "
        "reference, not a diagnosis of your vocal technique."
    ),
    "uk": (
        "Це приблизний показник того, наскільки схожий ваш тембр на референсний, "
        "а не діагностика вашої вокальної техніки."
    ),
}

# Spec 6.16: shown when A3 flags likely accompaniment in a `clean`-declared
# recording -- a warning, never a blocker or a score penalty (`clean`'s a
# cappella assumption, spec 2.3, is a product recommendation, not something
# the pipeline enforces by failing the analysis).
_ACCOMPANIMENT_IN_CLEAN_WARNING: dict[Locale, str] = {
    "en": (
        "Heads up: parts of your recording have energy that doesn't look like a "
        "clean solo voice. This app assumes `clean` recordings are a cappella, "
        "without background music or instruments picked up by the mic -- if "
        "that wasn't the case here, treat the scores above as less precise than "
        "usual, or retry this analysis in `mixed` mode."
    ),
    "uk": (
        "Зверніть увагу: частина вашого запису містить енергію, не схожу на "
        "чистий сольний голос. Цей застосунок вважає, що записи в режимі "
        "«а капела» зроблені без фонової музики чи інструментів, які могли "
        "потрапити в мікрофон -- якщо це не так, вважайте результати вище менш "
        "точними або повторіть аналіз у режимі «з музикою»."
    ),
}

# FR-41/6.19: unavailable aspects get their own block with the reason, never
# a silently missing section.
_UNAVAILABLE_REASON_TEXT: dict[Locale, dict[str, str]] = {
    "en": {
        "NOT_MEASURABLE_WITH_ACCOMPANIMENT": (
            "not measurable with accompaniment present in this recording"
        ),
    },
    "uk": {
        "NOT_MEASURABLE_WITH_ACCOMPANIMENT": (
            "неможливо виміряти через наявність супроводу в записі"
        ),
    },
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


_PITCH_TEMPLATES: dict[Locale, dict[str, str]] = {
    "en": {
        "excellent": (
            "Right on target: your pitch tracked the reference within {cents:.0f} cents on average."
        ),
        "good": (
            "Mostly accurate, {cents:.0f} cents off the reference on average. "
            "Listen back and isolate the handful of notes that drifted."
        ),
        "fair": (
            "Noticeably off pitch, {cents:.0f} cents off the reference on average. "
            "Practice the melody slowly against a piano or tuner before singing it at tempo."
        ),
        "poor": (
            "Pitch drifted by {cents:.0f} cents on average -- more than a semitone in places. "
            "Work on matching single sustained notes to a reference tone before the full phrase."
        ),
    },
    "uk": {
        "excellent": (
            "Точно в ціль: ваша висота тону трималася референсу в межах {cents:.0f} "
            "центів у середньому."
        ),
        "good": (
            "Здебільшого точно, відхилення {cents:.0f} центів від референсу в середньому. "
            "Прослухайте запис і знайдіть кілька нот, які зʼїхали."
        ),
        "fair": (
            "Помітно нечисто, відхилення {cents:.0f} центів від референсу в середньому. "
            "Розучіть мелодію повільно під піаніно чи тюнер, перш ніж співати в темпі."
        ),
        "poor": (
            "Висота тону відхилялася в середньому на {cents:.0f} центів -- місцями "
            "більше ніж на півтону. Попрацюйте над влучанням в окремі витримані ноти "
            "перед цілою фразою."
        ),
    },
}


def _pitch_feedback(data: dict[str, Any], locale: Locale) -> str:
    cents = data["mean_abs_cents"]
    tier = _tier(data["score"])
    return _PITCH_TEMPLATES[locale][tier].format(cents=cents)


_RHYTHM_TEMPLATES: dict[Locale, dict[str, str]] = {
    "en": {
        "excellent": "Timing was tight: {within}/{total} notes landed within the tolerance window.",
        "good": (
            "Timing was mostly solid, {offset_ms:.0f} ms off the reference on average "
            "({within}/{total} notes within tolerance). A metronome on the trickier lines "
            "will help."
        ),
        "fair": (
            "Entries were often early or late, {offset_ms:.0f} ms off the reference on average. "
            "Practice singing along with the reference track to internalize the exact timing."
        ),
        "poor": (
            "Timing diverged a lot from the reference, {offset_ms:.0f} ms off on average. "
            "Start by clapping or tapping the rhythm alone before adding the melody back in."
        ),
    },
    "uk": {
        "excellent": "Ритм був чітким: {within}/{total} нот потрапили у вікно допуску.",
        "good": (
            "Ритм здебільшого був стабільним, відхилення {offset_ms:.0f} мс від референсу "
            "в середньому ({within}/{total} нот у межах допуску). Метроном на складніших "
            "рядках допоможе."
        ),
        "fair": (
            "Вступи часто були зарано або запізно, відхилення {offset_ms:.0f} мс від "
            "референсу в середньому. Співайте разом з референсним треком, щоб засвоїти "
            "точний ритм."
        ),
        "poor": (
            "Ритм суттєво розійшовся з референсом, відхилення {offset_ms:.0f} мс у "
            "середньому. Почніть з простукування ритму окремо, перш ніж додавати мелодію."
        ),
    },
}


def _rhythm_feedback(data: dict[str, Any], locale: Locale) -> str:
    offset_ms = data["mean_abs_offset_ms"]
    within = data["onsets_within_tolerance"]
    total = data["reference_onset_count"]
    tier = _tier(data["score"])
    return _RHYTHM_TEMPLATES[locale][tier].format(offset_ms=offset_ms, within=within, total=total)


_BREATH_TEMPLATES: dict[Locale, dict[str, str]] = {
    "en": {
        "no_pauses": (
            "The reference has no clear breath points to compare against; nothing to flag here."
        ),
        "excellent": "Breaths landed where the reference takes them: {matched}/{total} matched.",
        "partial": (
            "Breathing was partly in the right places ({matched}/{total} matched). "
            "Mark the reference's breath points in your lyrics sheet for the rest."
        ),
        "poor": (
            "Breaths often didn't line up with the reference ({matched}/{total} matched). "
            "Plan where to breathe ahead of time, at the same phrase boundaries as the "
            "reference, and practice sustaining a full phrase on one breath."
        ),
    },
    "uk": {
        "no_pauses": (
            "У референсі немає чітких точок для дихання, з якими можна порівняти -- "
            "тут нема на що вказати."
        ),
        "excellent": "Дихання співпадало з референсом: {matched}/{total} збігів.",
        "partial": (
            "Дихання частково співпадало з референсом ({matched}/{total} збігів). "
            "Позначте точки дихання референсу у своєму тексті пісні для решти."
        ),
        "poor": (
            "Дихання часто не співпадало з референсом ({matched}/{total} збігів). "
            "Плануйте, де дихати, заздалегідь, на тих самих межах фраз, що й референс, "
            "і практикуйте витримування цілої фрази на одному диханні."
        ),
    },
}


def _breath_feedback(data: dict[str, Any], locale: Locale) -> str:
    matched = data["matched_pauses"]
    total = data["reference_pause_count"]
    templates = _BREATH_TEMPLATES[locale]
    if total == 0:
        return templates["no_pauses"]
    tier = _tier(data["score"])
    key = "excellent" if tier == _TIER_EXCELLENT else "poor" if tier == _TIER_POOR else "partial"
    return templates[key].format(matched=matched, total=total)


_DYNAMICS_TEMPLATES: dict[Locale, dict[str, str]] = {
    "en": {
        "excellent": "Your loudness contour closely followed the reference's crescendos and dips.",
        "partial": (
            "Dynamics partly tracked the reference (correlation {correlation:.2f}). "
            "Push a little harder into the loud passages and pull back further into the "
            "quiet ones."
        ),
        "poor": (
            "Your volume stayed fairly flat next to the reference (correlation "
            "{correlation:.2f}). Exaggerate the crescendos and diminuendos more than "
            "feels natural at first -- it usually still reads as normal from the "
            "listener's side."
        ),
    },
    "uk": {
        "excellent": "Гучність вашого голосу точно слідувала за наростаннями й спадами референсу.",
        "partial": (
            "Динаміка частково відповідала референсу (кореляція {correlation:.2f}). "
            "Співайте трохи гучніше в голосних місцях і тихіше в тихих."
        ),
        "poor": (
            "Гучність вашого голосу залишалася доволі рівною порівняно з референсом "
            "(кореляція {correlation:.2f}). Підкреслюйте наростання й спади сильніше, "
            "ніж здається природним спочатку -- зазвичай це все одно звучить нормально "
            "для слухача."
        ),
    },
}


def _dynamics_feedback(data: dict[str, Any], locale: Locale) -> str:
    correlation = data["correlation"]
    templates = _DYNAMICS_TEMPLATES[locale]
    tier = _tier(data["score"])
    key = "excellent" if tier == _TIER_EXCELLENT else "poor" if tier == _TIER_POOR else "partial"
    return templates[key].format(correlation=correlation)


_VIBRATO_TEMPLATES: dict[Locale, dict[str, str]] = {
    "en": {
        "user_added": (
            "You added vibrato where the reference sings a straight tone. "
            "Try holding a few sustained notes perfectly steady for practice."
        ),
        "neither": "Neither you nor the reference use vibrato here -- nothing to flag.",
        "user_missing": (
            "The reference uses vibrato on sustained notes that you sang straight. "
            "Vibrato usually comes from relaxed, breath-supported sustain -- "
            "practice holding a note and letting a gentle wave develop naturally."
        ),
        "close_match": "Your vibrato's rate and depth were a close match to the reference's.",
        "differs_rate_faster": "Vibrato was present but faster in rate than the reference's.",
        "differs_rate_slower": "Vibrato was present but slower in rate than the reference's.",
        "differs_depth_wider": "Vibrato was present but wider in depth than the reference's.",
        "differs_depth_narrower": "Vibrato was present but narrower in depth than the reference's.",
        "differs_rate_faster_depth_wider": (
            "Vibrato was present but faster in rate and wider in depth than the reference's."
        ),
        "differs_rate_faster_depth_narrower": (
            "Vibrato was present but faster in rate and narrower in depth than the reference's."
        ),
        "differs_rate_slower_depth_wider": (
            "Vibrato was present but slower in rate and wider in depth than the reference's."
        ),
        "differs_rate_slower_depth_narrower": (
            "Vibrato was present but slower in rate and narrower in depth than the reference's."
        ),
        "differs_character": (
            "Vibrato was present but a bit different in character than the reference's."
        ),
    },
    "uk": {
        "user_added": (
            "Ви додали вібрато там, де референс співає рівним тоном. "
            "Спробуйте потренуватися тримати кілька витриманих нот абсолютно рівно."
        ),
        "neither": "Ані ви, ані референс не використовуєте вібрато тут -- нема на що вказати.",
        "user_missing": (
            "Референс використовує вібрато на витриманих нотах, які ви заспівали рівно. "
            "Вібрато зазвичай виникає з розслабленого, підтриманого диханням витримування -- "
            "потренуйтеся тримати ноту і дати легкій хвилі розвинутися природно."
        ),
        "close_match": "Швидкість і глибина вашого вібрато були близькі до референсу.",
        "differs_rate_faster": "Вібрато було присутнє, але швидше за референсне.",
        "differs_rate_slower": "Вібрато було присутнє, але повільніше за референсне.",
        "differs_depth_wider": "Вібрато було присутнє, але глибше за референсне.",
        "differs_depth_narrower": "Вібрато було присутнє, але вужче за референсне.",
        "differs_rate_faster_depth_wider": (
            "Вібрато було присутнє, але швидше і глибше за референсне."
        ),
        "differs_rate_faster_depth_narrower": (
            "Вібрато було присутнє, але швидше і вужче за референсне."
        ),
        "differs_rate_slower_depth_wider": (
            "Вібрато було присутнє, але повільніше і глибше за референсне."
        ),
        "differs_rate_slower_depth_narrower": (
            "Вібрато було присутнє, але повільніше і вужче за референсне."
        ),
        "differs_character": "Вібрато було присутнє, але дещо іншого характеру, ніж референсне.",
    },
}


def _vibrato_outcome_key(user: dict[str, Any], reference: dict[str, Any]) -> str:
    """Locale-agnostic: which of vibrato's rate/depth combinations describes
    this take, as a template key -- never English words to splice into a
    sentence (that reads badly once translated word-by-word)."""
    rate_diff = user["rate_hz"] - reference["rate_hz"]
    depth_diff = user["depth_cents"] - reference["depth_cents"]
    rate = None
    if abs(rate_diff) > VIBRATO_RATE_TOLERANCE_HZ:
        rate = "faster" if rate_diff > 0 else "slower"
    depth = None
    if abs(depth_diff) > VIBRATO_DEPTH_TOLERANCE_CENTS:
        depth = "wider" if depth_diff > 0 else "narrower"
    if rate and depth:
        return f"differs_rate_{rate}_depth_{depth}"
    if rate:
        return f"differs_rate_{rate}"
    if depth:
        return f"differs_depth_{depth}"
    return "differs_character"


def _vibrato_feedback(data: dict[str, Any], locale: Locale) -> str:
    templates = _VIBRATO_TEMPLATES[locale]
    user, reference = data["user"], data["reference"]
    if not reference["detected"]:
        return templates["user_added"] if user["detected"] else templates["neither"]
    if not user["detected"]:
        return templates["user_missing"]
    if _tier(data["score"]) in (_TIER_EXCELLENT, _TIER_GOOD):
        return templates["close_match"]
    return templates[_vibrato_outcome_key(user, reference)]


_TIMBRE_TEMPLATES: dict[Locale, dict[str, str]] = {
    "en": {
        "close": "Your tone color was close to the reference's. {disclaimer}",
        "differs": (
            "Your tone color differed noticeably from the reference's. {disclaimer} "
            'A different natural voice is not something to "fix".'
        ),
    },
    "uk": {
        "close": "Ваш тембр був близьким до референсного. {disclaimer}",
        "differs": (
            "Ваш тембр помітно відрізнявся від референсного. {disclaimer} "
            'Інший природний голос -- це не те, що потрібно "виправляти".'
        ),
    },
}


def _timbre_feedback(data: dict[str, Any], locale: Locale) -> str:
    key = "close" if _tier(data["score"]) in (_TIER_EXCELLENT, _TIER_GOOD) else "differs"
    return _TIMBRE_TEMPLATES[locale][key].format(disclaimer=_TIMBRE_DISCLAIMER[locale])


_ASPECT_FEEDBACK: dict[str, Callable[[dict[str, Any], Locale], str]] = {
    "pitch": _pitch_feedback,
    "rhythm": _rhythm_feedback,
    "breath": _breath_feedback,
    "dynamics": _dynamics_feedback,
    "vibrato": _vibrato_feedback,
    "timbre": _timbre_feedback,
}

_OVERALL_SUMMARY_TEMPLATES: dict[Locale, str] = {
    "en": (
        "Overall score: {overall:.0f}/100. The biggest opportunity to improve is "
        "{focus} ({focus_score:.0f}/100)."
    ),
    "uk": (
        "Загальний результат: {overall:.0f}/100. Найбільше варто попрацювати над: "
        "{focus} ({focus_score:.0f}/100)."
    ),
}

_UNAVAILABLE_TEMPLATES: dict[Locale, str] = {
    "en": "{label}: not scored this time -- {reason}.",
    "uk": "{label}: не оцінено цього разу -- {reason}.",
}


def _overall_summary(aspect_scores: dict[str, float], overall_score: float, locale: Locale) -> str:
    focus_aspect = min(aspect_scores, key=lambda aspect: aspect_scores[aspect])
    focus_label = _ASPECT_LABELS[locale][focus_aspect]
    if locale == "en":
        focus_label = focus_label.lower()
    return _OVERALL_SUMMARY_TEMPLATES[locale].format(
        overall=overall_score, focus=focus_label, focus_score=aspect_scores[focus_aspect]
    )


def build_feedback_report(
    aspect_results: dict[str, StageResult],
    overall_score: float,
    *,
    aspects: Sequence[str],
    unavailable_aspects: dict[str, str],
    background_music_warning: bool = False,
    locale: Locale = DEFAULT_LOCALE,
) -> str:
    """Builds the FR-32 text report: one summary line, an optional spec 6.16
    warning, then one paragraph per *available* aspect (spec 6.14's
    per-mode set, in spec 6.4's canonical order), each grounded in that
    aspect's own stage data rather than generic advice, and finally one
    block per unavailable aspect explaining why (spec 6.19 -- never just
    silently missing). `aspect_results` has one entry per `aspects`, each
    carrying a `"score"` key. `locale` (ADR-0031) selects which language's
    phrase templates render the (locale-agnostic) outcome every aspect's
    own data already decided.
    """
    aspect_scores = {
        aspect: float(result.data["score"]) for aspect, result in aspect_results.items()
    }
    labels = _ASPECT_LABELS[locale]
    sections = [_overall_summary(aspect_scores, overall_score, locale)]
    if background_music_warning:
        sections.append(_ACCOMPANIMENT_IN_CLEAN_WARNING[locale])
    for aspect in aspects:
        result = aspect_results[aspect]
        label = labels[aspect]
        body: str = _ASPECT_FEEDBACK[aspect](result.data, locale)
        sections.append(f"{label} ({aspect_scores[aspect]:.0f}/100): {body}")
    for aspect, reason in unavailable_aspects.items():
        label = labels[aspect]
        reason_text = _UNAVAILABLE_REASON_TEXT[locale].get(reason, reason)
        sections.append(_UNAVAILABLE_TEMPLATES[locale].format(label=label, reason=reason_text))
    return "\n\n".join(sections)
