from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import sine_wave
from tests.helpers import make_context
from vocalcoach.errors import AlignmentFailed
from vocalcoach.models.results import StageStatus
from vocalcoach.pipeline.stages.align import AlignStage
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _through_separation(tmp_path: Path, recording, reference):
    context = make_context(tmp_path, recording_path=recording, reference_path=reference)
    context = context.with_result(PreprocessStage(ffmpeg_path="ffmpeg").run(context))
    context = context.with_result(FeaturesStage().run(context))
    return context


def test_align_matching_signals_produces_low_distance(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(4.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(4.0, 44100, 300.0), 44100)
    context = _through_separation(tmp_path, recording, reference)

    result = AlignStage().run(context)

    assert result.status == StageStatus.DONE
    assert len(result.data["index1"]) == len(result.data["index2"])
    assert result.data["index1"][0] == 0
    assert result.data["index2"][0] == 0
    # Monotonic non-decreasing warping path (a DTW invariant): compare each
    # index to its successor, so the two zipped sequences are one apart by
    # construction -- strict=True does not apply to this idiom.
    consecutive_pairs = zip(result.data["index1"], result.data["index1"][1:], strict=False)
    assert all(a <= b for a, b in consecutive_pairs)


def test_align_wildly_different_signals_raises_alignment_failed(tmp_path: Path, wav_writer) -> None:
    silence = wav_writer("recording.wav", sine_wave(4.0, 44100, 220.0, amplitude=0.0), 44100)
    noisy = wav_writer(
        "reference.wav",
        (0.5 * np.random.default_rng(0).standard_normal(4 * 44100)).astype("float32"),
        44100,
    )
    context = _through_separation(tmp_path, silence, noisy)

    with pytest.raises(AlignmentFailed):
        AlignStage().run(context)


def test_align_duration_beyond_window_raises_alignment_failed_not_internal(
    tmp_path: Path, wav_writer
) -> None:
    """dtw-python raises a bare ValueError ("No warping path found
    compatible with the local constraints"), not one of its own error
    types, when the two signals' length difference alone exceeds what the
    Sakoe-Chiba window (ALIGN_WINDOW_SECONDS) can bridge -- this must
    still classify as the non-retryable ALIGNMENT_FAILED (spec 6.8), not
    fall through as an uncaught, retried InternalPipelineError."""
    recording = wav_writer("recording.wav", sine_wave(2.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(20.0, 44100, 300.0), 44100)
    context = _through_separation(tmp_path, recording, reference)

    with pytest.raises(AlignmentFailed):
        AlignStage().run(context)
