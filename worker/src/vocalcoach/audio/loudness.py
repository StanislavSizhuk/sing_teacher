"""Loudness measurement and normalization (spec 6.3.1) via pyloudnorm's
ITU-R BS.1770 meter.
"""

from __future__ import annotations

import numpy as np
import pyloudnorm as pyln


def measure_and_normalize(
    samples: np.ndarray, sample_rate_hz: int, target_lufs: float
) -> tuple[np.ndarray, float]:
    """Measures integrated loudness and gain-adjusts to `target_lufs`.

    Returns `(normalized samples, the ORIGINAL measured loudness in LUFS)`.
    The original value is what callers use to detect a near-silent input
    (spec error codes `REFERENCE_TOO_QUIET`/`NO_VOICE_DETECTED`) -- if the
    caller only saw the post-normalization result, that signal would
    already be gone.
    """
    meter = pyln.Meter(sample_rate_hz)
    raw_loudness = float(meter.integrated_loudness(samples))
    if not np.isfinite(raw_loudness):
        # Digital silence: pyloudnorm's gain formula divides by a factor
        # derived from this value and would return NaN. Nothing to
        # normalize -- callers reject the input via the raw loudness anyway.
        return samples.astype(np.float32), raw_loudness
    normalized = pyln.normalize.loudness(samples, raw_loudness, target_lufs)
    return normalized.astype(np.float32), raw_loudness
