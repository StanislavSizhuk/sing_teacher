from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.conftest import sine_wave
from tests.helpers import make_context
from vocalcoach.dsp.features import compute_shared_features, load_shared_features
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def test_compute_shared_features_shapes(tmp_path: Path, wav_writer) -> None:
    path = wav_writer("tone.wav", sine_wave(2.0, 44100, 300.0), 44100)

    features = compute_shared_features(path)

    assert features.mfcc.ndim == 2
    assert features.mfcc.shape[1] == 13
    assert len(features.rms_envelope) == features.mfcc.shape[0]
    assert len(features.rms_fine) > len(features.rms_envelope)  # finer hop, more frames


def test_features_stage_writes_npz_both_sides_load_back(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(2.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(2.0, 44100, 320.0), 44100)
    context = make_context(tmp_path, recording_path=recording, reference_path=reference)
    context = context.with_result(PreprocessStage(ffmpeg_path="ffmpeg").run(context))

    result = FeaturesStage().run(context)

    features_path = Path(result.data["features_path"])
    assert features_path.exists()
    loaded = load_shared_features(features_path)
    assert loaded.user.mfcc.shape[1] == 13
    assert loaded.reference.mfcc.shape[1] == 13
    # Different frequencies -> different MFCC content, not accidentally
    # aliased to the same underlying array.
    assert not (loaded.user.mfcc == loaded.reference.mfcc).all()


def test_features_stage_is_picklable(tmp_path: Path) -> None:
    import pickle

    pickle.dumps(FeaturesStage())
