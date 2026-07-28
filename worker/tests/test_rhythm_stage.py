from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.helpers import build_context_through_align
from vocalcoach.pipeline.stages.rhythm import RhythmStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _percussive_pulses(
    duration_s: float, sample_rate_hz: int, onset_times: list[float]
) -> np.ndarray:
    """Sharp clicks at each onset time -- reliably onset-detectable, unlike
    a continuous tone (which librosa's onset detector handles poorly)."""
    signal = np.zeros(int(duration_s * sample_rate_hz), dtype=np.float32)
    click_len = int(0.02 * sample_rate_hz)
    for onset in onset_times:
        start = int(onset * sample_rate_hz)
        t = np.arange(click_len) / sample_rate_hz
        click = 0.8 * np.sin(2 * np.pi * 800 * t) * np.exp(-t * 40)
        signal[start : start + click_len] += click.astype(np.float32)
    return signal


def test_rhythm_matching_onsets_score_high(tmp_path: Path, wav_writer) -> None:
    onsets = [0.5, 1.0, 1.5, 2.0, 2.5]
    recording = wav_writer("recording.wav", _percussive_pulses(3.0, 44100, onsets), 44100)
    reference = wav_writer("reference.wav", _percussive_pulses(3.0, 44100, onsets), 44100)
    context = build_context_through_align(tmp_path, recording, reference)

    result = RhythmStage().run(context)

    assert result.data["score"] > 70
    assert result.data["reference_onset_count"] > 0
    assert result.data["user_onset_count"] > 0


def test_rhythm_jittered_onsets_score_lower(tmp_path: Path, wav_writer) -> None:
    # A *uniform* delay is exactly what DTW alignment is meant to absorb
    # (spec 6.3.4) -- it is not a rhythm problem, just a different start
    # offset. Irregular, per-onset jitter is what a genuine timing problem
    # looks like, since alignment cannot warp each note independently
    # enough to erase it.
    reference_onsets = [0.5, 1.0, 1.5, 2.0, 2.5]
    jitter = [0.18, -0.15, 0.20, -0.18, 0.16]
    jittered_onsets = [t + j for t, j in zip(reference_onsets, jitter, strict=True)]
    recording = wav_writer("recording.wav", _percussive_pulses(3.0, 44100, jittered_onsets), 44100)
    reference = wav_writer("reference.wav", _percussive_pulses(3.0, 44100, reference_onsets), 44100)
    context = build_context_through_align(tmp_path, recording, reference)

    result = RhythmStage().run(context)

    assert result.data["mean_abs_offset_ms"] > 50
    assert result.data["score"] < 90
