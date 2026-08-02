from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import sine_wave
from tests.helpers import build_context_through_align, reference_pitch_curve_for
from vocalcoach.pipeline.stages.pitch import PitchStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def test_pitch_matching_signals_score_near_perfect(tmp_path: Path, wav_writer) -> None:
    # Vibrato, not a bare constant tone: align now aligns on pitch contour
    # (ADR-0033), degenerate for a signal with no temporal pitch variation
    # at all -- see test_align_stage.py's own fixtures for the same reasoning.
    signal = sine_wave(4.0, 44100, 300.0, vibrato_hz=5.0, vibrato_cents=40.0)
    recording = wav_writer("recording.wav", signal, 44100)
    reference = wav_writer("reference.wav", signal, 44100)
    reference_pitch = reference_pitch_curve_for(tmp_path, reference)
    context = build_context_through_align(
        tmp_path, recording, reference, reference_pitch=reference_pitch
    )

    result = PitchStage().run(context)

    assert result.data["score"] > 85
    assert result.data["mean_abs_cents"] < 15

    piano_roll = result.data["piano_roll"]
    keys = ("user_hz", "reference_hz", "deviation_cents", "off_pitch")
    assert len({len(piano_roll[key]) for key in keys}) == 1
    # Matching signals: most frames should land a real (non-null) comparison,
    # and a near-perfect match should not flag many off-pitch frames.
    assert sum(1 for c in piano_roll["deviation_cents"] if c is not None) > 0
    assert sum(piano_roll["off_pitch"]) < len(piano_roll["off_pitch"]) * 0.1


def test_pitch_gates_detector_over_a_silent_gap(tmp_path: Path, wav_writer) -> None:
    """A real gap in the middle of an otherwise-sung recording (spec 6.5's
    VAD gate) must still produce a sane curve: voiced before/after the gap,
    unvoiced during it, and a fraction that reflects roughly how much of
    the recording was actually silent -- gating must not corrupt the result
    it's built to make cheaper.
    """
    tone = sine_wave(2.0, 44100, 300.0, vibrato_hz=5.0, vibrato_cents=40.0)
    silence = sine_wave(1.5, 44100, 300.0, amplitude=0.0)
    signal = np.concatenate([tone, silence, tone])
    recording = wav_writer("recording.wav", signal, 44100)
    reference = wav_writer("reference.wav", signal, 44100)
    context = build_context_through_align(tmp_path, recording, reference)

    result = PitchStage().run(context)

    # ~1.5s silent out of ~5.5s total -- comfortably clear of both "no
    # gating happened" (fraction near 1.0) and "everything got gated"
    # (fraction near 0.0).
    assert 0.5 < result.data["voiced_fraction"] < 0.85
    hz = result.data["user_pitch_curve"]["hz"]
    hop = result.data["user_pitch_curve"]["hop_seconds"]
    mid_frame = round(2.75 / hop)  # well inside the silent gap
    assert hz[mid_frame] is None
    first_frame = round(0.5 / hop)  # well inside the first sung span
    assert hz[first_frame] is not None
