from __future__ import annotations

import numpy as np

from tests.conftest import sine_wave
from vocalcoach.audio.loudness import measure_and_normalize


def test_measure_and_normalize_hits_target_loudness(sample_rate_hz: int) -> None:
    samples = sine_wave(3.0, sample_rate_hz, 440.0, amplitude=0.05)
    normalized, raw_loudness = measure_and_normalize(samples, sample_rate_hz, target_lufs=-23.0)

    assert raw_loudness < -23.0  # quiet input measures below the target before normalizing
    assert np.max(np.abs(normalized)) > np.max(np.abs(samples))  # got louder


def test_measure_and_normalize_digital_silence_does_not_crash(sample_rate_hz: int) -> None:
    samples = np.zeros(sample_rate_hz * 2, dtype=np.float32)
    normalized, raw_loudness = measure_and_normalize(samples, sample_rate_hz, target_lufs=-23.0)

    assert not np.isfinite(raw_loudness)
    assert np.all(normalized == 0.0)
