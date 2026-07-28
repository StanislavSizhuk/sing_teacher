from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.conftest import sine_wave
from tests.helpers import make_context
from vocalcoach.constants import PIPELINE_SAMPLE_RATE_HZ
from vocalcoach.models.results import StageStatus
from vocalcoach.pipeline.stages.preprocess import PreprocessStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def test_preprocess_resamples_and_normalizes(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(2.0, 44100, 220.0, amplitude=0.02), 44100)
    reference = wav_writer("reference.wav", sine_wave(2.0, 48000, 220.0, amplitude=0.02), 48000)
    context = make_context(tmp_path, recording_path=recording, reference_path=reference)

    result = PreprocessStage(ffmpeg_path="ffmpeg").run(context)

    assert result.status == StageStatus.DONE
    assert result.data["sample_rate_hz"] == PIPELINE_SAMPLE_RATE_HZ
    assert Path(result.data["recording_path"]).exists()
    assert Path(result.data["reference_path"]).exists()
    # Quiet input (amplitude 0.02) measures well below -23 LUFS before normalizing.
    assert result.data["recording_loudness_lufs"] < -23.0
    assert result.data["reference_loudness_lufs"] < -23.0
