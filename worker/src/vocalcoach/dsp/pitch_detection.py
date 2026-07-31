"""VAD-gated pitch detection (spec 6.5, A2/P4): shared by the warm path's
`PitchStage` (user recording) and the cold path's `PrepReferencePitchStage`
(reference vocal stem, spec 6.4 P4) -- both gate the same way, on the same
detector protocol, so the loop lives once here (spec 12.1 DRY).
"""

from __future__ import annotations

import numpy as np

from vocalcoach.dsp.vad import voiced_mask, voiced_spans
from vocalcoach.pipeline.registry import PitchDetector


def detect_gated(
    detector: PitchDetector,
    samples: np.ndarray,
    sample_rate_hz: int,
    hop_seconds: float,
    rms: np.ndarray,
) -> list[float | None]:
    """Runs `detector` only over the spans `rms`'s VAD mask (spec 6.5) marks
    voiced, filling every other frame with `None` directly instead of
    running the detector over silence it would only report as unvoiced
    anyway. Each span is detected independently rather than one call over a
    concatenation of all of them, so a span's own length is the only frame
    count its output ever needs to line up with.
    """
    mask = voiced_mask(rms, hop_seconds)
    total_frames = len(mask)
    result: list[float | None] = [None] * total_frames

    for start, end in voiced_spans(mask):
        hop_length = max(1, round(sample_rate_hz * hop_seconds))
        chunk = samples[start * hop_length : end * hop_length]
        if len(chunk) == 0:
            continue
        detected = detector.detect(chunk, sample_rate_hz, hop_seconds)
        span_len = end - start
        for i, value in enumerate(detected[:span_len]):
            result[start + i] = value
    return result
