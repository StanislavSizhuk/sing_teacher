"""Shared fixtures: synthetic, deterministic signals only -- no real audio
fixtures in the repo (spec 15.2), no network access from any test here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


def sine_wave(
    duration_s: float,
    sample_rate_hz: int,
    frequency_hz: float,
    *,
    amplitude: float = 0.3,
    vibrato_hz: float = 0.0,
    vibrato_cents: float = 0.0,
    silence_at: float | None = None,
    silence_duration_s: float = 0.4,
) -> np.ndarray:
    """A mono sine tone, optionally with vibrato (FM) and a silent gap --
    exactly what spec 15.2 asks for: "a generated sine with known pitch."
    """
    t = np.arange(int(duration_s * sample_rate_hz)) / sample_rate_hz
    freq = frequency_hz * (2.0 ** ((vibrato_cents / 1200.0) * np.sin(2 * np.pi * vibrato_hz * t)))
    phase = 2 * np.pi * np.cumsum(freq) / sample_rate_hz
    signal = amplitude * np.sin(phase)

    if silence_at is not None:
        start = int(silence_at * sample_rate_hz)
        end = start + int(silence_duration_s * sample_rate_hz)
        signal[start:end] = 0.0

    return signal.astype(np.float32)


@pytest.fixture
def sample_rate_hz() -> int:
    return 22050


@pytest.fixture
def wav_writer(tmp_path: Path):
    """Writes a numpy array to a temp WAV file and returns its path."""

    def _write(name: str, samples: np.ndarray, sample_rate_hz: int) -> Path:
        path = tmp_path / name
        sf.write(path, samples, sample_rate_hz, subtype="PCM_16")
        return path

    return _write
