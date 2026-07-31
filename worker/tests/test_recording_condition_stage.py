from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.conftest import sine_wave
from tests.helpers import make_context, reference_pitch_curve_for
from vocalcoach.constants import PITCH_HOP_SECONDS
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.models.audio import PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.registry import PyinPitchDetector
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.pitch import PitchStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.recording_condition import RecordingConditionStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _context_through_features(
    tmp_path: Path,
    recording: Path,
    reference: Path,
    *,
    reference_pitch: PitchCurve | None = None,
) -> AnalysisContext:
    context = make_context(
        tmp_path,
        recording_path=recording,
        reference_path=reference,
        reference_pitch=reference_pitch,
    )
    context = context.with_result(PreprocessStage(ffmpeg_path="ffmpeg").run(context))
    return context.with_result(FeaturesStage().run(context))


def _with_pitch_curve(context: AnalysisContext, hz: list[float | None]) -> AnalysisContext:
    return context.with_result(
        StageResult(
            stage="pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"user_pitch_curve": {"hop_seconds": PITCH_HOP_SECONDS, "hz": hz}},
        )
    )


def _frame_count(context: AnalysisContext) -> int:
    features_path = Path(context.result("features").data["features_path"])
    return len(load_shared_features(features_path).user.rms_fine)


def test_flags_loud_unvoiced_recording_as_likely_background_music(
    tmp_path: Path, wav_writer
) -> None:
    # A loud tone the fake pitch curve below claims is unvoiced throughout --
    # stands in for an instrumental/noisy recording without needing a real
    # (expensive, model-based) pitch detector to actually call it unvoiced.
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 220.0, amplitude=0.8), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 220.0), 44100)
    context = _context_through_features(tmp_path, recording, reference)
    context = _with_pitch_curve(context, [None] * _frame_count(context))

    result = RecordingConditionStage().run(context)

    assert result.data["background_music_detected"] is True
    assert result.data["non_vocal_energy_fraction"] > 0.9


def test_does_not_flag_quiet_unvoiced_frames(tmp_path: Path, wav_writer) -> None:
    # Near-silence: unvoiced, but quiet -- a natural pause, not contamination.
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 220.0, amplitude=0.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 220.0), 44100)
    context = _context_through_features(tmp_path, recording, reference)
    context = _with_pitch_curve(context, [None] * _frame_count(context))

    result = RecordingConditionStage().run(context)

    assert result.data["background_music_detected"] is False
    assert result.data["non_vocal_energy_fraction"] == 0.0


def test_does_not_flag_loud_voiced_recording(tmp_path: Path, wav_writer) -> None:
    # Loud, but every frame is "voiced" -- a normal, energetic singing voice.
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 220.0, amplitude=0.8), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 220.0), 44100)
    context = _context_through_features(tmp_path, recording, reference)
    context = _with_pitch_curve(context, [220.0] * _frame_count(context))

    result = RecordingConditionStage().run(context)

    assert result.data["background_music_detected"] is False
    assert result.data["non_vocal_energy_fraction"] == 0.0


def test_real_pitch_stage_on_clean_tone_does_not_flag(tmp_path: Path, wav_writer) -> None:
    """End-to-end with the real (non-faked) pitch detector, not just a
    hand-built curve: a clean sustained tone must not trip the heuristic."""
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 300.0), 44100)
    reference_pitch = reference_pitch_curve_for(tmp_path, reference)
    context = _context_through_features(
        tmp_path, recording, reference, reference_pitch=reference_pitch
    )
    context = context.with_result(
        StageResult(
            stage="align",
            status=StageStatus.DONE,
            duration_ms=1,
            data={
                "index1": list(range(200)),
                "index2": list(range(200)),
                "hop_seconds": 0.05,
            },
        )
    )
    pitch_result = PitchStage(PyinPitchDetector()).run(context)
    context = context.with_result(pitch_result)

    result = RecordingConditionStage().run(context)

    assert result.data["background_music_detected"] is False
