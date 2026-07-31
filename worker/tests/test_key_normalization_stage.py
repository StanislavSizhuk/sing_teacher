"""T1-T3 (spec 15.2): key-shift normalization (A8, spec 6.8) -- the most
dangerous correction in the pipeline, so its guard conditions get direct
tests, not just its happy path. Built directly on synthetic
`deviation_cents` (as stage "pitch" would have already computed them, spec
6.3.5) rather than real audio: what these tests check is the shift
arithmetic and its guard conditions, not pitch-detection accuracy.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tests.helpers import EMPTY_REFERENCE_PITCH
from vocalcoach.dsp.pitch_scoring import score_from_mean_abs_cents
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.mode import Mode
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.stages.key_normalization import KeyNormalizationStage

_MIN_SEMITONES = 0.6
_MAX_IQR_SEMITONES = 0.5
_MAX_SEMITONES = 7.0


def _stage() -> KeyNormalizationStage:
    return KeyNormalizationStage(_MIN_SEMITONES, _MAX_IQR_SEMITONES, _MAX_SEMITONES)


def _context_with_deviation_cents(
    deviation_cents: list[float], *, mode: Mode = "mixed", allow_transposition: bool = False
) -> AnalysisContext:
    context = AnalysisContext(
        analysis_id="a",
        user_id="u",
        song_id="s",
        recording_path=Path("recording.wav"),
        work_dir=Path("work"),
        reference_vocal_stem_path=Path("ref.wav"),
        reference_pitch=EMPTY_REFERENCE_PITCH,
        mode=mode,
        allow_transposition=allow_transposition,
    )
    return context.with_result(
        StageResult(
            stage="pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"piano_roll": {"deviation_cents": deviation_cents}},
        )
    )


def _residual_intonation_cents(n: int = 200, spread_cents: float = 15.0) -> list[float]:
    """Small, realistic sing-along inaccuracy around perfect pitch -- no
    systematic transposition, just normal intonation wobble."""
    rng = np.random.default_rng(0)
    return [float(v) for v in rng.normal(0.0, spread_cents, n)]


@pytest.mark.parametrize("shift_semitones", [3.0, -5.0])
def test_t1_transposition_is_recovered_and_pitch_score_barely_moves(
    shift_semitones: float,
) -> None:
    """T1 / NFR-20: transposing the same vocal by +3 or -5 semitones must
    not move the (shift-corrected) pitch score by more than 3 points from
    what the untransposed take would have scored."""
    residual = _residual_intonation_cents()
    baseline_mean_abs_cents = sum(abs(c) for c in residual) / len(residual)
    baseline_score = score_from_mean_abs_cents(baseline_mean_abs_cents)

    shifted = [c + shift_semitones * 100.0 for c in residual]
    context = _context_with_deviation_cents(shifted, mode="mixed")

    result = _stage().run(context)

    assert result.data["applied"] is True
    assert result.data["key_shift_semitones"] == pytest.approx(shift_semitones, abs=0.2)
    assert abs(result.data["adjusted_score"] - baseline_score) <= 3.0


def test_t2_stable_small_flatness_is_not_forgiven() -> None:
    """T2: a *stable* -0.4 semitone offset is real intonation error, not a
    transposition -- spec 6.8's KEY_SHIFT_MIN_SEMITONES (0.6) must reject it
    even though it is perfectly stable (low IQR)."""
    residual = _residual_intonation_cents(spread_cents=5.0)  # stable: low IQR
    flat = [c - 40.0 for c in residual]  # -0.4 semitone median offset
    context = _context_with_deviation_cents(flat, mode="mixed")

    result = _stage().run(context)

    assert result.data["applied"] is False
    assert result.data["key_shift_semitones"] is None
    assert abs(result.data["median_semitones"] - (-0.4)) < 0.1


def test_t3_wandering_pitch_with_zero_median_is_not_forgiven() -> None:
    """T3: a wide, unstable spread that happens to average out to zero is
    pitch wandering, not a transposition -- KEY_SHIFT_MAX_IQR must reject it
    even though the median alone would look harmless."""
    # Alternating +/- 600 cents: median is ~0 but IQR is huge.
    wandering = [600.0 if i % 2 == 0 else -600.0 for i in range(200)]
    context = _context_with_deviation_cents(wandering, mode="mixed")

    result = _stage().run(context)

    assert result.data["applied"] is False
    assert result.data["iqr_semitones"] > _MAX_IQR_SEMITONES


def test_shift_not_applied_in_clean_without_allow_transposition() -> None:
    """spec 6.8: `clean` defaults to no transposition at all -- a real,
    stable, in-range shift is still not applied unless the user opted in."""
    residual = _residual_intonation_cents()
    shifted = [c + 300.0 for c in residual]
    context = _context_with_deviation_cents(shifted, mode="clean", allow_transposition=False)

    result = _stage().run(context)

    assert result.data["applied"] is False


def test_shift_applied_in_clean_with_allow_transposition() -> None:
    residual = _residual_intonation_cents()
    shifted = [c + 300.0 for c in residual]
    context = _context_with_deviation_cents(shifted, mode="clean", allow_transposition=True)

    result = _stage().run(context)

    assert result.data["applied"] is True


def test_shift_out_of_range_is_not_applied_and_flagged() -> None:
    """spec 6.8: a median past MAX_KEY_SHIFT_SEMITONES is treated as a
    broken alignment, not a transposition -- KEY_SHIFT_OUT_OF_RANGE (spec
    6.15/6.18), no shift applied."""
    residual = _residual_intonation_cents(spread_cents=5.0)
    shifted = [c + 900.0 for c in residual]  # +9 semitones, past the 7 max
    context = _context_with_deviation_cents(shifted, mode="mixed")

    result = _stage().run(context)

    assert result.data["applied"] is False
    assert result.data["out_of_range"] is True
