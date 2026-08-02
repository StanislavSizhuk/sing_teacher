"""Embeds a pitch curve as points on the unit circle, one full turn per
octave (ADR-0033): lets `align`'s banded DTW (`dsp/dtw.py`) run its plain
Euclidean-distance kernel, unchanged, directly on melody instead of MFCC.

`PITCH_FMIN_HZ` is only a fixed phase reference for the embedding angle --
any positive frequency gives the identical octave-invariance property,
this just reuses an existing constant instead of introducing an arbitrary
new one.
"""

from __future__ import annotations

import numpy as np

from vocalcoach.constants import PITCH_FMIN_HZ

#: The embedding's distance from the origin to any point on the unit
#: circle -- exactly the Euclidean distance an unvoiced (0, 0) frame gets
#: from any voiced frame, regardless of that frame's own pitch. Not a
#: tuned constant: it is the radius of the circle this module always
#: embeds onto.
UNVOICED_TO_VOICED_DISTANCE = 1.0


def embed_pitch_curve(hz: list[float | None]) -> np.ndarray:
    """Returns `(len(hz), 2)` float32: row `i` is `(cos(theta), sin(theta))`
    for voiced frame `i`, `theta = 2*pi * frac(log2(hz[i] / PITCH_FMIN_HZ))`
    -- the fractional part of `log2` is the pitch *class*, so two pitches a
    whole number of octaves apart embed to the same point. An unvoiced
    frame (`hz[i] is None`) embeds to `(0, 0)`, the circle's center: exactly
    `UNVOICED_TO_VOICED_DISTANCE` from every voiced point (a real, moderate
    mismatch), and `0.0` from another unvoiced frame (a real match) -- both
    fall out of plain Euclidean distance on this embedding, no special case
    needed in the DTW kernel.
    """
    embedding = np.zeros((len(hz), 2), dtype=np.float32)
    for i, value in enumerate(hz):
        if value is None or value <= 0:
            continue
        theta = 2.0 * np.pi * (np.log2(value / PITCH_FMIN_HZ) % 1.0)
        embedding[i, 0] = np.cos(theta)
        embedding[i, 1] = np.sin(theta)
    return embedding
