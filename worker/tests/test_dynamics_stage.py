from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.helpers import build_context_through_align, build_context_with_identity_align
from vocalcoach.pipeline.stages.dynamics import DynamicsStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _crescendo(duration_s: float, sample_rate_hz: int, frequency_hz: float) -> np.ndarray:
    t = np.arange(int(duration_s * sample_rate_hz)) / sample_rate_hz
    envelope = np.linspace(0.02, 0.5, len(t))
    return (envelope * np.sin(2 * np.pi * frequency_hz * t)).astype(np.float32)


def _flat_loud(duration_s: float, sample_rate_hz: int, frequency_hz: float) -> np.ndarray:
    t = np.arange(int(duration_s * sample_rate_hz)) / sample_rate_hz
    return (0.3 * np.sin(2 * np.pi * frequency_hz * t)).astype(np.float32)


def test_dynamics_matching_envelope_scores_high(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", _crescendo(3.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", _crescendo(3.0, 44100, 300.0), 44100)
    context = build_context_through_align(tmp_path, recording, reference)

    result = DynamicsStage().run(context)

    assert result.data["correlation"] > 0.8
    assert result.data["score"] > 80


def test_dynamics_flat_vs_crescendo_scores_lower(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", _flat_loud(3.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", _crescendo(3.0, 44100, 300.0), 44100)
    # A flat-vs-crescendo pair also differs enough in MFCC energy to fail
    # real DTW alignment -- that's align's own concern, not dynamics'; use
    # an identity mapping to test this stage's comparison in isolation.
    context = build_context_with_identity_align(tmp_path, recording, reference)

    result = DynamicsStage().run(context)

    assert result.data["correlation"] < 0.8
