from __future__ import annotations

import math

from vocalcoach.models.audio import PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.stages.vibrato import VibratoStage


def _pitch_curve_with_vibrato(
    duration_s: float, hop_seconds: float, note_hz: float, vibrato_hz: float, vibrato_cents: float
) -> PitchCurve:
    n = int(duration_s / hop_seconds)
    hz = []
    for i in range(n):
        t = i * hop_seconds
        cents_offset = vibrato_cents * math.sin(2 * math.pi * vibrato_hz * t)
        hz.append(note_hz * (2.0 ** (cents_offset / 1200.0)))
    return PitchCurve(hop_seconds=hop_seconds, hz=hz)


def _context_with_pitch_result(
    tmp_path, user_curve: PitchCurve, reference_curve: PitchCurve
) -> AnalysisContext:
    context = AnalysisContext(
        analysis_id="a",
        user_id="u",
        song_id="s",
        recording_path=tmp_path / "r.wav",
        work_dir=tmp_path / "work",
        reference_vocal_stem_path=tmp_path / "ref.wav",
        reference_pitch=reference_curve,
    )
    return context.with_result(
        StageResult(
            stage="pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"user_pitch_curve": user_curve.model_dump(mode="json")},
        )
    )


def test_vibrato_matching_profiles_score_high(tmp_path) -> None:
    user = _pitch_curve_with_vibrato(2.0, 0.01, 300.0, 5.5, 50.0)
    reference = _pitch_curve_with_vibrato(2.0, 0.01, 300.0, 5.5, 50.0)
    context = _context_with_pitch_result(tmp_path, user, reference)

    result = VibratoStage().run(context)

    assert result.data["score"] > 80
    assert result.data["user"]["detected"] is True
    assert result.data["reference"]["detected"] is True


def test_vibrato_presence_mismatch_scores_low(tmp_path) -> None:
    flat = PitchCurve(hop_seconds=0.01, hz=[300.0] * 200)  # no oscillation at all
    vibrating = _pitch_curve_with_vibrato(2.0, 0.01, 300.0, 5.5, 60.0)
    context = _context_with_pitch_result(tmp_path, flat, vibrating)

    result = VibratoStage().run(context)

    assert result.data["reference"]["detected"] is True
    assert result.data["user"]["detected"] is False
    assert result.data["score"] < 60


def test_vibrato_no_vibrato_either_side_scores_perfect(tmp_path) -> None:
    flat_user = PitchCurve(hop_seconds=0.01, hz=[300.0] * 200)
    flat_reference = PitchCurve(hop_seconds=0.01, hz=[300.0] * 200)
    context = _context_with_pitch_result(tmp_path, flat_user, flat_reference)

    result = VibratoStage().run(context)

    assert result.data["score"] == 100.0
