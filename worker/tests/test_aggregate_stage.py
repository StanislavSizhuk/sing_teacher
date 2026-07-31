from __future__ import annotations

from pathlib import Path

from tests.helpers import EMPTY_REFERENCE_PITCH
from vocalcoach.config import ScoringWeights
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.report import build_feedback_report
from vocalcoach.pipeline.stages.aggregate import AggregateStage

_WEIGHTS = {
    "clean": ScoringWeights.parse(
        "pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10", "clean"
    ),
    "mixed": ScoringWeights.parse("pitch:0.50,rhythm:0.30,dynamics:0.10,vibrato:0.10", "mixed"),
}


def _stage(name: str, score: float, **extra: object) -> StageResult:
    return StageResult(
        stage=name, status=StageStatus.DONE, duration_ms=1, data={"score": score, **extra}
    )


def _key_normalization_result(applied: bool = False) -> StageResult:
    return StageResult(
        stage="key_normalization",
        status=StageStatus.DONE,
        duration_ms=1,
        data={
            "applied": applied,
            "key_shift_semitones": None,
            "median_semitones": 0.0,
            "iqr_semitones": 0.0,
            "out_of_range": False,
            "adjusted_score": None,
            "adjusted_mean_abs_cents": None,
        },
    )


def _recording_condition_result(
    *, accompaniment_detected: bool = False, effective_mode: str = "clean", warnings=()
) -> StageResult:
    return StageResult(
        stage="recording_condition",
        status=StageStatus.DONE,
        duration_ms=1,
        data={
            "accompaniment_level": 0.0,
            "accompaniment_detected": accompaniment_detected,
            "effective_mode": effective_mode,
            "warnings": list(warnings),
        },
    )


def _align_result(normalized_distance: float = 5.0) -> StageResult:
    return StageResult(
        stage="align",
        status=StageStatus.DONE,
        duration_ms=1,
        data={"normalized_distance": normalized_distance, "coarse_normalized_distance": 5.0},
    )


def _context_with_aspect_results(tmp_path: Path, mode: str = "clean") -> AnalysisContext:
    context = AnalysisContext(
        analysis_id="a",
        user_id="u",
        song_id="s",
        recording_path=tmp_path / "r.wav",
        work_dir=tmp_path / "work",
        reference_vocal_stem_path=tmp_path / "ref.wav",
        reference_pitch=EMPTY_REFERENCE_PITCH,
        mode=mode,
    )
    results = [
        _stage("pitch", 80.0, mean_abs_cents=12.0, voiced_fraction=0.9),
        _stage(
            "rhythm",
            90.0,
            mean_abs_offset_ms=40.0,
            onsets_within_tolerance=18,
            reference_onset_count=20,
            user_onset_count=19,
        ),
        _stage("breath", 70.0, matched_pauses=7, reference_pause_count=10, user_pause_count=9),
        _stage("dynamics", 60.0, correlation=0.6),
        _stage(
            "vibrato",
            100.0,
            user={"detected": True, "rate_hz": 5.5, "depth_cents": 40.0},
            reference={"detected": True, "rate_hz": 5.6, "depth_cents": 42.0},
        ),
        _stage("timbre", 50.0, mean_cosine_similarity=0.5),
        _recording_condition_result(),
        _key_normalization_result(),
        _align_result(),
    ]
    for result in results:
        context = context.with_result(result)
    return context


def test_aggregate_computes_weighted_overall_score(tmp_path: Path) -> None:
    context = _context_with_aspect_results(tmp_path)

    result = AggregateStage(_WEIGHTS, scoring_version="2.0").run(context)

    # 80*.35 + 90*.20 + 70*.15 + 60*.10 + 100*.10 + 50*.10 = 77.5
    assert result.data["overall_score"] == 77.5
    assert result.data["scoring_version"] == "2.0"
    assert result.data["weights_profile"] == "clean_v1"
    assert result.data["unavailable_aspects"] == {}
    assert result.data["aspect_scores"] == {
        "pitch": 80.0,
        "rhythm": 90.0,
        "breath": 70.0,
        "dynamics": 60.0,
        "vibrato": 100.0,
        "timbre": 50.0,
    }


def test_aggregate_report_names_lowest_scoring_aspect_as_focus(tmp_path: Path) -> None:
    context = _context_with_aspect_results(tmp_path)

    result = AggregateStage(_WEIGHTS, scoring_version="2.0").run(context)

    report = result.data["feedback_text"]
    assert "Overall score: 78/100" in report  # 77.5 rounds to 78 under "{:.0f}"
    assert "timbre" in report.lower()  # timbre is the lowest-scoring aspect (50)


def test_aggregate_report_flags_accompaniment_in_clean_without_penalizing_score(
    tmp_path: Path,
) -> None:
    context = _context_with_aspect_results(tmp_path)
    context = context.with_result(
        _recording_condition_result(
            accompaniment_detected=True, warnings=["ACCOMPANIMENT_IN_CLEAN_MODE"]
        )
    )

    result = AggregateStage(_WEIGHTS, scoring_version="2.0").run(context)

    # spec 6.16: a report warning, never a score penalty -- same 77.5 as the
    # accompaniment_detected=False fixture above.
    assert result.data["overall_score"] == 77.5
    assert "doesn't look like a clean solo voice" in result.data["feedback_text"]
    assert "ACCOMPANIMENT_IN_CLEAN_MODE" in result.data["warnings"]
    assert result.data["confidence"] == "medium"  # high, stepped down once


def test_aggregate_report_omits_accompaniment_warning_when_not_detected(tmp_path: Path) -> None:
    context = _context_with_aspect_results(tmp_path)

    result = AggregateStage(_WEIGHTS, scoring_version="2.0").run(context)

    assert "doesn't look like a clean solo voice" not in result.data["feedback_text"]
    assert result.data["confidence"] == "high"


def test_aggregate_report_includes_timbre_disclaimer(tmp_path: Path) -> None:
    context = _context_with_aspect_results(tmp_path)

    result = AggregateStage(_WEIGHTS, scoring_version="2.0").run(context)

    assert "not a diagnosis of your vocal technique" in result.data["feedback_text"]


def test_aggregate_report_covers_every_aspect_by_label(tmp_path: Path) -> None:
    context = _context_with_aspect_results(tmp_path)

    result = AggregateStage(_WEIGHTS, scoring_version="2.0").run(context)

    report = result.data["feedback_text"]
    for label in (
        "Pitch accuracy",
        "Rhythm and timing",
        "Breath and phrasing",
        "Dynamics",
        "Vibrato",
        "Timbre",
    ):
        assert label in report


def test_aggregate_uses_key_normalization_adjusted_pitch_score(tmp_path: Path) -> None:
    context = _context_with_aspect_results(tmp_path)
    context = context.with_result(
        StageResult(
            stage="key_normalization",
            status=StageStatus.DONE,
            duration_ms=1,
            data={
                "applied": True,
                "key_shift_semitones": -2.0,
                "median_semitones": -2.0,
                "iqr_semitones": 0.1,
                "out_of_range": False,
                "adjusted_score": 95.0,
                "adjusted_mean_abs_cents": 5.0,
            },
        )
    )

    result = AggregateStage(_WEIGHTS, scoring_version="2.0").run(context)

    assert result.data["aspect_scores"]["pitch"] == 95.0
    assert result.data["key_shift_semitones"] == -2.0
    # 95*.35 + 90*.20 + 70*.15 + 60*.10 + 100*.10 + 50*.10 = 82.75
    assert result.data["overall_score"] == 82.8


def test_report_flags_vibrato_presence_mismatch() -> None:
    aspect_results = {
        "pitch": _stage("pitch", 100.0, mean_abs_cents=0.0),
        "rhythm": _stage(
            "rhythm",
            100.0,
            mean_abs_offset_ms=0.0,
            onsets_within_tolerance=1,
            reference_onset_count=1,
            user_onset_count=1,
        ),
        "breath": _stage(
            "breath", 100.0, matched_pauses=1, reference_pause_count=1, user_pause_count=1
        ),
        "dynamics": _stage("dynamics", 100.0, correlation=1.0),
        "vibrato": _stage(
            "vibrato",
            40.0,
            user={"detected": False, "rate_hz": None, "depth_cents": None},
            reference={"detected": True, "rate_hz": 5.5, "depth_cents": 40.0},
        ),
        "timbre": _stage("timbre", 100.0, mean_cosine_similarity=1.0),
    }

    report = build_feedback_report(
        aspect_results,
        overall_score=90.0,
        aspects=("pitch", "rhythm", "breath", "dynamics", "vibrato", "timbre"),
        unavailable_aspects={},
    )

    assert "vibrato on sustained notes that you sang straight" in report


def test_report_handles_reference_with_no_breath_points() -> None:
    aspect_results = {
        "pitch": _stage("pitch", 100.0, mean_abs_cents=0.0),
        "rhythm": _stage(
            "rhythm",
            100.0,
            mean_abs_offset_ms=0.0,
            onsets_within_tolerance=1,
            reference_onset_count=1,
            user_onset_count=1,
        ),
        "breath": _stage(
            "breath", 100.0, matched_pauses=0, reference_pause_count=0, user_pause_count=0
        ),
        "dynamics": _stage("dynamics", 100.0, correlation=1.0),
        "vibrato": _stage(
            "vibrato",
            100.0,
            user={"detected": False, "rate_hz": None, "depth_cents": None},
            reference={"detected": False, "rate_hz": None, "depth_cents": None},
        ),
        "timbre": _stage("timbre", 100.0, mean_cosine_similarity=1.0),
    }

    report = build_feedback_report(
        aspect_results,
        overall_score=100.0,
        aspects=("pitch", "rhythm", "breath", "dynamics", "vibrato", "timbre"),
        unavailable_aspects={},
    )

    assert "no clear breath points to compare against" in report


def test_t7_mixed_mode_reports_null_aspects_and_uses_mixed_v1_profile(tmp_path: Path) -> None:
    """T7 (spec 15.2): in `mixed`, breath and timbre are `null` with a
    reason (FR-41) -- never `0` -- and `overall_score` is computed under
    `mixed_v1`, over exactly the four aspects that ran."""
    context = AnalysisContext(
        analysis_id="a",
        user_id="u",
        song_id="s",
        recording_path=tmp_path / "r.wav",
        work_dir=tmp_path / "work",
        reference_vocal_stem_path=tmp_path / "ref.wav",
        reference_pitch=EMPTY_REFERENCE_PITCH,
        mode="mixed",
    )
    for result in (
        _stage("pitch", 80.0, mean_abs_cents=12.0, voiced_fraction=0.9),
        _stage(
            "rhythm",
            90.0,
            mean_abs_offset_ms=40.0,
            onsets_within_tolerance=18,
            reference_onset_count=20,
            user_onset_count=19,
        ),
        _stage("dynamics", 60.0, correlation=0.6),
        _stage(
            "vibrato",
            100.0,
            user={"detected": True, "rate_hz": 5.5, "depth_cents": 40.0},
            reference={"detected": True, "rate_hz": 5.6, "depth_cents": 42.0},
        ),
        _recording_condition_result(effective_mode="mixed"),
        _key_normalization_result(),
        _align_result(),
    ):
        context = context.with_result(result)

    result = AggregateStage(_WEIGHTS, scoring_version="2.0").run(context)

    assert result.data["weights_profile"] == "mixed_v1"
    assert result.data["unavailable_aspects"] == {
        "breath": "NOT_MEASURABLE_WITH_ACCOMPANIMENT",
        "timbre": "NOT_MEASURABLE_WITH_ACCOMPANIMENT",
    }
    assert "breath" not in result.data["aspect_scores"]
    assert "timbre" not in result.data["aspect_scores"]
    # 80*.50 + 90*.30 + 60*.10 + 100*.10 = 83.0
    assert result.data["overall_score"] == 83.0
    assert result.data["confidence"] == "medium"  # mode=mixed caps at medium
    report = result.data["feedback_text"]
    assert "Breath and phrasing: not scored this time" in report
    assert "Timbre: not scored this time" in report


def test_report_lists_unavailable_aspects_with_reason() -> None:
    aspect_results = {
        "pitch": _stage("pitch", 100.0, mean_abs_cents=0.0),
        "rhythm": _stage(
            "rhythm",
            100.0,
            mean_abs_offset_ms=0.0,
            onsets_within_tolerance=1,
            reference_onset_count=1,
            user_onset_count=1,
        ),
    }

    report = build_feedback_report(
        aspect_results,
        overall_score=100.0,
        aspects=("pitch", "rhythm"),
        unavailable_aspects={
            "breath": "NOT_MEASURABLE_WITH_ACCOMPANIMENT",
            "timbre": "NOT_MEASURABLE_WITH_ACCOMPANIMENT",
        },
    )

    assert "Breath and phrasing: not scored this time" in report
    assert "Timbre: not scored this time" in report
    assert "not measurable with accompaniment" in report
