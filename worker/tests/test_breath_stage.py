from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.helpers import build_context_with_identity_align
from vocalcoach.pipeline.stages.breath import BreathStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _phrases(
    duration_s: float, sample_rate_hz: int, pause_at: list[tuple[float, float]]
) -> np.ndarray:
    t = np.arange(int(duration_s * sample_rate_hz)) / sample_rate_hz
    signal = 0.3 * np.sin(2 * np.pi * 300.0 * t)
    for start, end in pause_at:
        signal[int(start * sample_rate_hz) : int(end * sample_rate_hz)] = 0.0
    return signal.astype(np.float32)


def test_breath_matching_pauses_score_high(tmp_path: Path, wav_writer) -> None:
    pauses = [(1.0, 1.4), (2.2, 2.6)]
    recording = wav_writer("recording.wav", _phrases(4.0, 44100, pauses), 44100)
    reference = wav_writer("reference.wav", _phrases(4.0, 44100, pauses), 44100)
    # ADR-0033: align now aligns on pitch contour, which needs genuine
    # pitch variation to be non-degenerate -- this fixture's constant tone
    # is tuned for breath's own pause-detection logic, not for that, and
    # this test cares about pause matching through *a* time map, not
    # align's own accuracy (that is test_align_stage.py's job).
    context = build_context_with_identity_align(tmp_path, recording, reference)

    result = BreathStage().run(context)

    assert result.data["score"] == 100.0
    assert result.data["reference_pause_count"] >= 1
    assert result.data["matched_pauses"] == result.data["reference_pause_count"]


def test_breath_missing_pause_scores_lower(tmp_path: Path, wav_writer) -> None:
    reference_pauses = [(1.0, 1.4), (2.2, 2.6)]
    # sings straight through, no pauses at all
    recording = wav_writer("recording.wav", _phrases(4.0, 44100, []), 44100)
    reference = wav_writer("reference.wav", _phrases(4.0, 44100, reference_pauses), 44100)
    # ADR-0033: align now aligns on pitch contour, which needs genuine
    # pitch variation to be non-degenerate -- this fixture's constant tone
    # is tuned for breath's own pause-detection logic, not for that, and
    # this test cares about pause matching through *a* time map, not
    # align's own accuracy (that is test_align_stage.py's job).
    context = build_context_with_identity_align(tmp_path, recording, reference)

    result = BreathStage().run(context)

    assert result.data["score"] < 100.0
    assert result.data["matched_pauses"] < result.data["reference_pause_count"]
