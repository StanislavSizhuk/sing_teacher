from __future__ import annotations

from pathlib import Path

from tests.helpers import EMPTY_REFERENCE_PITCH
from vocalcoach.config import ScoringWeights
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.report import build_feedback_report
from vocalcoach.pipeline.stages.aggregate import AggregateStage

_WEIGHTS = ScoringWeights.parse(
    "pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10"
)


def _stage(name: str, score: float, **extra: object) -> StageResult:
    return StageResult(
        stage=name, status=StageStatus.DONE, duration_ms=1, data={"score": score, **extra}
    )


def _context_with_aspect_results(tmp_path: Path) -> AnalysisContext:
    context = AnalysisContext(
        analysis_id="a",
        user_id="u",
        song_id="s",
        recording_path=tmp_path / "r.wav",
        work_dir=tmp_path / "work",
        reference_vocal_stem_path=tmp_path / "ref.wav",
        reference_pitch=EMPTY_REFERENCE_PITCH,
    )
    results = [
        _stage("pitch", 80.0, mean_abs_cents=12.0),
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
        StageResult(
            stage="recording_condition",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"background_music_detected": False, "non_vocal_energy_fraction": 0.0},
        ),
    ]
    for result in results:
        context = context.with_result(result)
    return context


def test_aggregate_computes_weighted_overall_score(tmp_path: Path) -> None:
    context = _context_with_aspect_results(tmp_path)

    result = AggregateStage(_WEIGHTS, scoring_version="1.0").run(context)

    # 80*.35 + 90*.20 + 70*.15 + 60*.10 + 100*.10 + 50*.10 = 77.5
    assert result.data["overall_score"] == 77.5
    assert result.data["scoring_version"] == "1.0"
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

    result = AggregateStage(_WEIGHTS, scoring_version="1.0").run(context)

    report = result.data["feedback_text"]
    assert "Overall score: 78/100" in report  # 77.5 rounds to 78 under "{:.0f}"
    assert "timbre" in report.lower()  # timbre is the lowest-scoring aspect (50)


def test_aggregate_report_flags_background_music_without_penalizing_score(tmp_path: Path) -> None:
    context = _context_with_aspect_results(tmp_path)
    context = context.with_result(
        StageResult(
            stage="recording_condition",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"background_music_detected": True, "non_vocal_energy_fraction": 0.42},
        )
    )

    result = AggregateStage(_WEIGHTS, scoring_version="1.0").run(context)

    # spec 6.9: a report warning, never a score penalty -- same 77.5 as the
    # background_music_detected=False fixture above.
    assert result.data["overall_score"] == 77.5
    assert "doesn't look like a clean solo voice" in result.data["feedback_text"]


def test_aggregate_report_omits_background_music_warning_when_not_detected(
    tmp_path: Path,
) -> None:
    context = _context_with_aspect_results(tmp_path)

    result = AggregateStage(_WEIGHTS, scoring_version="1.0").run(context)

    assert "doesn't look like a clean solo voice" not in result.data["feedback_text"]


def test_aggregate_report_includes_timbre_disclaimer(tmp_path: Path) -> None:
    context = _context_with_aspect_results(tmp_path)

    result = AggregateStage(_WEIGHTS, scoring_version="1.0").run(context)

    assert "not a diagnosis of your vocal technique" in result.data["feedback_text"]


def test_aggregate_report_covers_every_aspect_by_label(tmp_path: Path) -> None:
    context = _context_with_aspect_results(tmp_path)

    result = AggregateStage(_WEIGHTS, scoring_version="1.0").run(context)

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

    report = build_feedback_report(aspect_results, overall_score=90.0)

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

    report = build_feedback_report(aspect_results, overall_score=100.0)

    assert "no clear breath points to compare against" in report
