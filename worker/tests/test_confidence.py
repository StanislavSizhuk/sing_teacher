from __future__ import annotations

from vocalcoach.scoring.confidence import ConfidenceSignals, compute_confidence


def _signals(**overrides: object) -> ConfidenceSignals:
    base: dict[str, object] = {
        "mode": "clean",
        "accompaniment_in_clean": False,
        "voiced_ratio": 0.9,
        "alignment_cost": 5.0,
        "key_shift_out_of_range": False,
        "length_mismatch": False,
        "reference_start_offset_detected": False,
    }
    base.update(overrides)
    return ConfidenceSignals(**base)  # type: ignore[arg-type]


def test_clean_mode_no_signals_is_high_confidence_no_warnings() -> None:
    result = compute_confidence(_signals())

    assert result.overall == "high"
    assert result.warnings == ()


def test_mixed_mode_caps_confidence_at_medium_before_any_other_signal() -> None:
    result = compute_confidence(_signals(mode="mixed"))

    assert result.overall == "medium"
    assert result.warnings == ()


def test_length_mismatch_steps_down_confidence_and_warns() -> None:
    result = compute_confidence(_signals(length_mismatch=True))

    assert result.overall == "medium"  # high, stepped down once
    assert result.warnings == ("LENGTH_MISMATCH_PARTIAL_ANALYSIS",)


def test_length_mismatch_combines_with_other_signals() -> None:
    result = compute_confidence(_signals(length_mismatch=True, key_shift_out_of_range=True))

    assert result.overall == "low"  # high, stepped down twice
    assert set(result.warnings) == {"LENGTH_MISMATCH_PARTIAL_ANALYSIS", "KEY_SHIFT_OUT_OF_RANGE"}


def test_weak_alignment_steps_down_confidence_and_warns() -> None:
    result = compute_confidence(_signals(alignment_cost=50.0))

    assert result.overall == "medium"
    assert result.warnings == ("WEAK_ALIGNMENT",)


def test_reference_start_offset_steps_down_confidence_and_warns() -> None:
    result = compute_confidence(_signals(reference_start_offset_detected=True))

    assert result.overall == "medium"  # high, stepped down once
    assert result.warnings == ("REFERENCE_START_OFFSET_DETECTED",)
