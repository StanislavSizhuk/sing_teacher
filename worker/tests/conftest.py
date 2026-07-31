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


def harmonic_tone(
    f0_curve: np.ndarray,
    sample_rate_hz: int,
    *,
    amplitude: float = 0.3,
    n_harmonics: int = 5,
    harmonic_decay: float = 0.6,
) -> np.ndarray:
    """A harmonic-rich tone tracking `f0_curve` (one Hz value per sample),
    each successive harmonic quieter than the last -- closer to a sung voice
    than `sine_wave`'s single partial, which matters for anything exercising
    harmonic structure (spec 6.6 melody extraction, T4)."""
    phase = 2 * np.pi * np.cumsum(f0_curve) / sample_rate_hz
    signal = np.zeros(len(f0_curve), dtype=np.float64)
    for harmonic in range(1, n_harmonics + 1):
        signal += (harmonic_decay ** (harmonic - 1)) * np.sin(harmonic * phase)
    signal *= amplitude / np.max(np.abs(signal))
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
