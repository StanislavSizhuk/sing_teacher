from __future__ import annotations

import numpy as np

from vocalcoach.dsp.vad import voiced_mask, voiced_spans


def _rms(*, loud_frames: int, silent_frames: int, loud_level: float = 0.3) -> np.ndarray:
    return np.concatenate(
        [
            np.full(loud_frames, loud_level, dtype=np.float32),
            np.zeros(silent_frames, dtype=np.float32),
            np.full(loud_frames, loud_level, dtype=np.float32),
        ]
    )


def test_all_silent_track_has_empty_mask() -> None:
    rms = np.zeros(50, dtype=np.float32)
    mask = voiced_mask(rms, hop_seconds=0.01)
    assert not mask.any()
    assert voiced_spans(mask) == []


def test_all_loud_track_is_fully_voiced() -> None:
    rms = np.full(50, 0.3, dtype=np.float32)
    mask = voiced_mask(rms, hop_seconds=0.01)
    assert mask.all()
    assert voiced_spans(mask) == [(0, 50)]


def test_long_silent_gap_between_phrases_is_gated() -> None:
    # 0.5s of loud, 1s of silence (well over VAD_MIN_SILENT_RUN_SECONDS),
    # 0.5s of loud, at a 10ms hop.
    rms = _rms(loud_frames=50, silent_frames=100)
    mask = voiced_mask(rms, hop_seconds=0.01)

    spans = voiced_spans(mask)
    assert spans == [(0, 50), (150, 200)]


def test_short_silent_blip_is_not_worth_gating() -> None:
    # A 50ms gap is well under VAD_MIN_SILENT_RUN_SECONDS (0.3s) -- gating
    # it would save nothing and risks clipping a real onset at the seam.
    rms = _rms(loud_frames=50, silent_frames=5)
    mask = voiced_mask(rms, hop_seconds=0.01)

    assert mask.all()
    assert voiced_spans(mask) == [(0, 105)]


def test_empty_rms_returns_empty_mask() -> None:
    mask = voiced_mask(np.zeros(0, dtype=np.float32), hop_seconds=0.01)
    assert len(mask) == 0
    assert voiced_spans(mask) == []
