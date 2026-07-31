from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import sine_wave
from tests.helpers import FakeVocalSeparator
from vocalcoach.errors import ReferenceTooQuiet
from vocalcoach.models.context import SongPrepContext
from vocalcoach.models.results import StageStatus
from vocalcoach.pipeline.stages.prep_reference import PrepReferenceStage
from vocalcoach.pipeline.stages.separate_reference import SeparateReferenceStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


class SilentSeparator:
    def separate_vocals(self, mixture: np.ndarray, sample_rate_hz: int) -> np.ndarray:
        return np.zeros_like(mixture)

    def release(self) -> None:
        pass


def _preprocessed_context(tmp_path: Path, wav_writer) -> SongPrepContext:
    reference = wav_writer("reference.wav", sine_wave(2.0, 44100, 220.0), 44100)
    context = SongPrepContext(
        song_id="test-song",
        reference_path=reference,
        work_dir=tmp_path / "work",
        vocal_stem_path=tmp_path / "stem.wav",
    )
    result = PrepReferenceStage(ffmpeg_path="ffmpeg").run(context)
    return context.with_result(result)


def test_separate_reference_writes_stem(tmp_path: Path, wav_writer) -> None:
    context = _preprocessed_context(tmp_path, wav_writer)
    stage = SeparateReferenceStage(FakeVocalSeparator())

    result = stage.run(context)

    assert result.status == StageStatus.DONE
    stem_path = Path(result.data["stem_path"])
    assert stem_path.exists()
    assert stem_path == context.vocal_stem_path


def test_separate_reference_raises_on_silent_stem(tmp_path: Path, wav_writer) -> None:
    context = _preprocessed_context(tmp_path, wav_writer)
    stage = SeparateReferenceStage(SilentSeparator())

    with pytest.raises(ReferenceTooQuiet):
        stage.run(context)
