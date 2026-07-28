from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import sine_wave
from tests.helpers import FakeVocalSeparator, make_context
from vocalcoach.errors import ReferenceTooQuiet
from vocalcoach.models.results import StageStatus
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.separate_reference import SeparateReferenceStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


class SilentSeparator:
    def separate_vocals(self, mixture: np.ndarray, sample_rate_hz: int) -> np.ndarray:
        return np.zeros_like(mixture)

    def release(self) -> None:
        pass


def _preprocessed_context(tmp_path: Path, wav_writer):
    recording = wav_writer("recording.wav", sine_wave(2.0, 44100, 220.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(2.0, 44100, 220.0), 44100)
    context = make_context(tmp_path, recording_path=recording, reference_path=reference)
    result = PreprocessStage(ffmpeg_path="ffmpeg").run(context)
    return context.with_result(result)


def test_separate_reference_writes_stem(tmp_path: Path, wav_writer) -> None:
    context = _preprocessed_context(tmp_path, wav_writer)
    stage = SeparateReferenceStage(
        FakeVocalSeparator(), stem_path_for_song=lambda song_id: tmp_path / f"stem-{song_id}.wav"
    )

    result = stage.run(context)

    assert result.status == StageStatus.DONE
    assert result.data["cached"] is False
    stem_path = Path(result.data["stem_path"])
    assert stem_path.exists()


def test_separate_reference_skips_when_already_cached(tmp_path: Path, wav_writer) -> None:
    context = _preprocessed_context(tmp_path, wav_writer)
    context = context.model_copy(update={"vocal_stem_processed": True})
    stage = SeparateReferenceStage(
        FakeVocalSeparator(), stem_path_for_song=lambda song_id: tmp_path / f"stem-{song_id}.wav"
    )

    result = stage.run(context)

    assert result.data["cached"] is True
    # The cached path is only referenced, not written by this run.
    assert not Path(result.data["stem_path"]).exists()


def test_separate_reference_raises_on_silent_stem(tmp_path: Path, wav_writer) -> None:
    context = _preprocessed_context(tmp_path, wav_writer)
    stage = SeparateReferenceStage(
        SilentSeparator(), stem_path_for_song=lambda song_id: tmp_path / f"stem-{song_id}.wav"
    )

    with pytest.raises(ReferenceTooQuiet):
        stage.run(context)
