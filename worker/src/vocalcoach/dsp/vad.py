"""Energy-based voice-activity gate (spec 6.5, stage A2): lets the pipeline
skip its most expensive per-frame stage -- pitch detection -- over stretches
that are silent anyway. A typical vocal take spends 30-50% of its length in
silence between phrases (spec 6.6), and per-frame pitch detection is by far
the single most expensive stage measured on real audio (see
docs/PERFORMANCE.md).

Interim implementation, not Silero VAD: spec 6.6 names Silero (ONNX) as the
eventual v2.0 engine, but that arrives with the cold/warm path split (M2),
once a model-weights volume and an ONNX runtime are already being
introduced for other reasons. This module reuses the same relative-RMS-to-
peak technique `breath.py`'s pause detection already uses -- no new model
dependency, and a real, measurable win now. See ADR-0023.
"""

from __future__ import annotations

import numpy as np

from vocalcoach.constants import BREATH_SILENCE_RELATIVE_DB, VAD_MIN_SILENT_RUN_SECONDS


def voiced_mask(rms: np.ndarray, hop_seconds: float) -> np.ndarray:
    """One bool per `rms` frame: `True` where the pitch detector should
    actually run. A frame quieter than `BREATH_SILENCE_RELATIVE_DB` relative
    to the recording's own peak counts as silent; a silent run shorter than
    `VAD_MIN_SILENT_RUN_SECONDS` is folded back to `True` anyway -- gating a
    handful of frames saves nothing and only risks clipping a real onset
    right at the boundary.
    """
    if len(rms) == 0:
        return np.zeros(0, dtype=bool)

    peak = float(np.max(rms))
    if peak <= 0:
        return np.zeros(len(rms), dtype=bool)

    with np.errstate(divide="ignore"):
        relative_db = 20.0 * np.log10(rms / peak)
    loud_enough = relative_db >= BREATH_SILENCE_RELATIVE_DB

    min_silent_run = max(1, round(VAD_MIN_SILENT_RUN_SECONDS / hop_seconds))
    mask = loud_enough.copy()
    run_start: int | None = None
    for i, loud in enumerate(loud_enough):
        if not loud and run_start is None:
            run_start = i
        elif loud and run_start is not None:
            if i - run_start < min_silent_run:
                mask[run_start:i] = True  # too short a gap to bother gating
            run_start = None
    if run_start is not None and len(loud_enough) - run_start < min_silent_run:
        mask[run_start:] = True
    return mask


def voiced_spans(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous `[start, end)` frame-index spans where `mask` is `True`."""
    spans: list[tuple[int, int]] = []
    run_start: int | None = None
    for i, voiced in enumerate(mask):
        if voiced and run_start is None:
            run_start = i
        elif not voiced and run_start is not None:
            spans.append((run_start, i))
            run_start = None
    if run_start is not None:
        spans.append((run_start, len(mask)))
    return spans
