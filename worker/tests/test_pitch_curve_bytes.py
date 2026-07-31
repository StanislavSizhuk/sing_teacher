from __future__ import annotations

from vocalcoach.models.audio import PitchCurve


def test_round_trip_preserves_voiced_values_and_none() -> None:
    curve = PitchCurve(hop_seconds=0.01, hz=[440.0, None, 441.5, None, 220.25])

    data, meta = curve.to_bytes()
    restored = PitchCurve.from_bytes(data, meta)

    assert restored == curve


def test_bytes_are_four_per_frame_float32() -> None:
    curve = PitchCurve(hop_seconds=0.01, hz=[1.0, 2.0, 3.0])
    data, _meta = curve.to_bytes()
    assert len(data) == 3 * 4


def test_meta_carries_hop_seconds_and_length() -> None:
    curve = PitchCurve(hop_seconds=0.05, hz=[100.0, None])
    _data, meta = curve.to_bytes()
    assert meta["hop_seconds"] == 0.05
    assert meta["length"] == 2


def test_empty_curve_round_trips() -> None:
    curve = PitchCurve(hop_seconds=0.01, hz=[])
    data, meta = curve.to_bytes()
    assert PitchCurve.from_bytes(data, meta) == curve
