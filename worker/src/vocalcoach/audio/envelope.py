"""RMS loudness envelope, shared by stage 8 (dynamics) and stage 10 (breath)."""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np

from vocalcoach.audio.io import read_mono


def rms_envelope(path: Path, hop_seconds: float) -> np.ndarray:
    """Frame-wise RMS amplitude, one value every `hop_seconds`."""
    samples, sample_rate = read_mono(path)
    hop_length = max(1, round(sample_rate * hop_seconds))
    rms = librosa.feature.rms(y=samples, hop_length=hop_length)[0]
    return np.asarray(rms)
