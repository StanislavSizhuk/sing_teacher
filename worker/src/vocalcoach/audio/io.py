"""WAV read/write helpers shared by every pipeline stage.

All stages work in mono float32 PCM at a known sample rate (Hz) -- spec 6.3
stage 1 is the only place resampling happens; every later stage trusts its
input is already canonical.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    """Reads a WAV file as mono float32 PCM.

    Args:
        path: WAV file path.

    Returns:
        (samples, sample_rate_hz). Multi-channel input is averaged down to
        mono -- every file reaching this function past stage 1 is already
        mono, but stage 1 itself reads the pre-pipeline upload, which is not.
    """
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    return mono, sample_rate


def write_mono(path: Path, samples: np.ndarray, sample_rate_hz: int) -> None:
    """Writes mono float32 PCM samples as a 16-bit PCM WAV file."""
    sf.write(path, samples, sample_rate_hz, subtype="PCM_16")
