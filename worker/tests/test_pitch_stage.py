from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.conftest import sine_wave
from tests.helpers import build_context_through_align, make_context
from vocalcoach.errors import NoVoiceDetected
from vocalcoach.models.audio import PitchCurve
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.registry import PyinPitchDetector
from vocalcoach.pipeline.stages.pitch import PitchStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def test_pitch_matching_signals_score_near_perfect(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(4.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(4.0, 44100, 300.0), 44100)
    context = build_context_through_align(tmp_path, recording, reference)

    result = PitchStage(PyinPitchDetector()).run(context)

    assert result.data["score"] > 85
    assert result.data["mean_abs_cents"] < 15
    # Cache warmed for future analyses of this song (spec 6.6) -- persisting
    # it isn't this stage's job (see pitch.py), so only the "not cached"
    # signal is this stage's to produce; test_handler.py covers the write.
    assert result.data["reference_cached"] is False

    piano_roll = result.data["piano_roll"]
    keys = ("user_hz", "reference_hz", "deviation_cents", "off_pitch")
    assert len({len(piano_roll[key]) for key in keys}) == 1
    # Matching signals: most frames should land a real (non-null) comparison,
    # and a near-perfect match should not flag many off-pitch frames.
    assert sum(1 for c in piano_roll["deviation_cents"] if c is not None) > 0
    assert sum(piano_roll["off_pitch"]) < len(piano_roll["off_pitch"]) * 0.1


def test_pitch_reuses_cached_reference_curve_when_warm(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(4.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(4.0, 44100, 300.0), 44100)
    context = build_context_through_align(tmp_path, recording, reference)

    # Reuse the pitch curve from the first (cold) run as the "cached" one.
    cold_result = PitchStage(PyinPitchDetector()).run(context)
    reference_pitch = PitchCurve.model_validate(cold_result.data["reference_pitch_curve"])
    warm_context = context.model_copy(
        update={"vocal_stem_processed": True, "reference_pitch": reference_pitch}
    )

    result = PitchStage(PyinPitchDetector()).run(warm_context)
    assert result.data["score"] > 85
    assert result.data["reference_cached"] is True


def test_pitch_raises_no_voice_detected_on_silence(tmp_path: Path, wav_writer) -> None:
    # A silent recording also fails DTW alignment (nothing to align against),
    # which would mask NO_VOICE_DETECTED behind ALIGNMENT_FAILED if routed
    # through the real align stage -- inject a trivial identity mapping
    # instead, to test pitch's own voiced-fraction check in isolation.
    silence_path = wav_writer("recording.wav", sine_wave(3.0, 22050, 300.0, amplitude=0.0), 22050)
    reference_path = wav_writer("reference.wav", sine_wave(3.0, 22050, 300.0), 22050)
    context = make_context(tmp_path, recording_path=silence_path, reference_path=reference_path)
    context = context.with_result(
        StageResult(
            stage="preprocess",
            status=StageStatus.DONE,
            duration_ms=1,
            data={
                "recording_path": str(silence_path),
                "reference_path": str(reference_path),
                "sample_rate_hz": 22050,
            },
        )
    )
    context = context.with_result(
        StageResult(
            stage="separate_reference",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"stem_path": str(reference_path)},
        )
    )
    context = context.with_result(
        StageResult(
            stage="align",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"index1": [0], "index2": [0], "hop_seconds": 0.05},
        )
    )

    with pytest.raises(NoVoiceDetected):
        PitchStage(PyinPitchDetector()).run(context)
