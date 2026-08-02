from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import sine_wave
from tests.helpers import FakeVocalSeparator, make_context
from vocalcoach.errors import NoVoiceDetected
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageStatus
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.separate_recording import SeparateRecordingStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


class SilentSeparator:
    def separate_vocals(self, mixture: np.ndarray, sample_rate_hz: int) -> np.ndarray:
        return np.zeros_like(mixture)

    def release(self) -> None:
        pass


def _preprocessed_context(tmp_path: Path, wav_writer) -> AnalysisContext:
    recording = wav_writer("recording.wav", sine_wave(2.0, 44100, 220.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(2.0, 44100, 220.0), 44100)
    context = make_context(
        tmp_path, recording_path=recording, reference_path=reference, mode="mixed"
    )
    result = PreprocessStage(ffmpeg_path="ffmpeg").run(context)
    return context.with_result(result)


def test_separate_recording_writes_stem(tmp_path: Path, wav_writer) -> None:
    context = _preprocessed_context(tmp_path, wav_writer)
    stage = SeparateRecordingStage(FakeVocalSeparator())

    result = stage.run(context)

    assert result.status == StageStatus.DONE
    stem_path = Path(result.data["stem_path"])
    assert stem_path.exists()
    assert stem_path.parent == context.work_dir


def test_separate_recording_raises_on_silent_stem(tmp_path: Path, wav_writer) -> None:
    context = _preprocessed_context(tmp_path, wav_writer)
    stage = SeparateRecordingStage(SilentSeparator())

    with pytest.raises(NoVoiceDetected):
        stage.run(context)
