from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.conftest import sine_wave
from tests.helpers import FakeSongRepository, build_context_through_align, make_context
from vocalcoach.errors import NoVoiceDetected
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.registry import PyinPitchDetector
from vocalcoach.pipeline.stages.pitch import PitchStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def test_pitch_matching_signals_score_near_perfect(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(4.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(4.0, 44100, 300.0), 44100)
    context = build_context_through_align(tmp_path, recording, reference)
    songs = FakeSongRepository()

    result = PitchStage(PyinPitchDetector(), songs).run(context)

    assert result.data["score"] > 85
    assert result.data["mean_abs_cents"] < 15
    assert songs.saved_pitch_curve is not None  # cache warmed for future analyses of this song


def test_pitch_caches_reference_curve_and_reuses_it_when_warm(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(4.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(4.0, 44100, 300.0), 44100)
    context = build_context_through_align(tmp_path, recording, reference)

    class ExplodingSongRepo:
        def get_by_id(self, song_id):  # pragma: no cover
            raise NotImplementedError

        def save_lyrics(self, song_id, lyrics):  # pragma: no cover
            raise NotImplementedError

        def mark_vocal_stem_processed(self, song_id, reference_pitch):
            raise AssertionError("must not write the cache again on a cache hit")

    # Reuse the pitch curve from the first (cold) run as the "cached" one.
    warm_songs = FakeSongRepository()
    PitchStage(PyinPitchDetector(), warm_songs).run(context)

    context = context.model_copy(
        update={"vocal_stem_processed": True, "reference_pitch": warm_songs.saved_pitch_curve}
    )

    result = PitchStage(PyinPitchDetector(), ExplodingSongRepo()).run(context)
    assert result.data["score"] > 85


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
        PitchStage(PyinPitchDetector(), FakeSongRepository()).run(context)
