from __future__ import annotations

import re
from collections.abc import Mapping

from vocalcoach.models.locale import Locale
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline import report
from vocalcoach.pipeline.report import build_feedback_report

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")

# Every module-level dict[Locale, ...] template table report.py defines --
# ADR-0031's own safety net for the compile-time exhaustiveness check
# Python dict literals don't otherwise get (unlike web/'s
# `Translations = typeof en`).
# Mapping (covariant in its value type), not dict, so mypy can type this
# list at all: the tables mix dict[Locale, str] and dict[Locale, dict[str,
# str]], and dict's invariant value type joins those to plain `object`.
_LOCALE_TEMPLATE_DICTS: list[Mapping[Locale, object]] = [
    report._ASPECT_LABELS,
    report._TIMBRE_DISCLAIMER,
    report._ACCOMPANIMENT_IN_CLEAN_WARNING,
    report._UNAVAILABLE_REASON_TEXT,
    report._PITCH_TEMPLATES,
    report._RHYTHM_TEMPLATES,
    report._BREATH_TEMPLATES,
    report._DYNAMICS_TEMPLATES,
    report._VIBRATO_TEMPLATES,
    report._TIMBRE_TEMPLATES,
    report._OVERALL_SUMMARY_TEMPLATES,
    report._UNAVAILABLE_TEMPLATES,
]


def test_every_locale_template_table_has_the_same_keys_in_both_locales() -> None:
    for table in _LOCALE_TEMPLATE_DICTS:
        assert set(table.keys()) == {"en", "uk"}, table
        first, *_rest = table.values()
        if isinstance(first, dict):
            expected_keys = set(first.keys())
            for locale, nested in table.items():
                assert isinstance(nested, dict)
                assert set(nested.keys()) == expected_keys, (table, locale)


def _stage(name: str, **data: object) -> StageResult:
    return StageResult(stage=name, status=StageStatus.DONE, duration_ms=1, data=data)


def _aspect_results() -> dict[str, StageResult]:
    return {
        "pitch": _stage("pitch", score=82.0, mean_abs_cents=18.0),
        "rhythm": _stage(
            "rhythm",
            score=88.0,
            mean_abs_offset_ms=25.0,
            onsets_within_tolerance=40,
            reference_onset_count=42,
        ),
        "breath": _stage("breath", score=60.0, matched_pauses=3, reference_pause_count=5),
        "dynamics": _stage("dynamics", score=55.0, correlation=0.5),
        "vibrato": _stage(
            "vibrato",
            score=40.0,
            user={"detected": True, "rate_hz": 6.5, "depth_cents": 90.0},
            reference={"detected": True, "rate_hz": 5.0, "depth_cents": 60.0},
        ),
        "timbre": _stage("timbre", score=91.0),
    }


def test_build_feedback_report_defaults_to_english() -> None:
    text = build_feedback_report(
        _aspect_results(), 70.0, aspects=list(_aspect_results()), unavailable_aspects={}
    )
    assert text.startswith("Overall score: 70/100.")
    assert not _CYRILLIC.search(text)


def test_build_feedback_report_renders_ukrainian() -> None:
    text = build_feedback_report(
        _aspect_results(),
        70.0,
        aspects=list(_aspect_results()),
        unavailable_aspects={},
        locale="uk",
    )
    assert text.startswith("Загальний результат: 70/100.")
    assert _CYRILLIC.search(text)


def test_unavailable_aspect_reason_is_translated() -> None:
    results = _aspect_results()
    del results["timbre"]
    text = build_feedback_report(
        results,
        70.0,
        aspects=[a for a in results],
        unavailable_aspects={"timbre": "NOT_MEASURABLE_WITH_ACCOMPANIMENT"},
        locale="uk",
    )
    assert "неможливо виміряти через наявність супроводу в записі" in text


def test_background_music_warning_is_translated() -> None:
    text = build_feedback_report(
        _aspect_results(),
        70.0,
        aspects=list(_aspect_results()),
        unavailable_aspects={},
        background_music_warning=True,
        locale="uk",
    )
    assert "Зверніть увагу" in text


def test_vibrato_outcome_key_covers_every_rate_depth_combination() -> None:
    faster_wider = report._vibrato_outcome_key(
        {"rate_hz": 8.0, "depth_cents": 120.0}, {"rate_hz": 5.0, "depth_cents": 50.0}
    )
    assert faster_wider == "differs_rate_faster_depth_wider"

    slower_narrower = report._vibrato_outcome_key(
        {"rate_hz": 2.0, "depth_cents": 10.0}, {"rate_hz": 5.0, "depth_cents": 70.0}
    )
    assert slower_narrower == "differs_rate_slower_depth_narrower"

    character_only = report._vibrato_outcome_key(
        {"rate_hz": 5.0, "depth_cents": 50.0}, {"rate_hz": 5.0, "depth_cents": 50.0}
    )
    assert character_only == "differs_character"
