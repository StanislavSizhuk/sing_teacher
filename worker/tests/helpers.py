"""Shared test doubles and a helper to build an `AnalysisContext` through
stage 4 (align), so per-aspect stage tests (5-10) don't each re-derive
preprocessing/separation/alignment from scratch.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from vocalcoach.constants import FEATURES_HOP_SECONDS
from vocalcoach.models.audio import Lyrics, PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.stages.align import AlignStage
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.separate_reference import SeparateReferenceStage


class FakeVocalSeparator:
    """Stands in for Demucs: this synthetic audio has no instruments to
    remove, so "separation" is the identity function (spec 15.2: stage
    tests never touch the real model)."""

    def separate_vocals(self, mixture: np.ndarray, sample_rate_hz: int) -> np.ndarray:
        return mixture

    def release(self) -> None:
        pass


class FakeTranscriber:
    def __init__(self, lyrics: Lyrics | None = None) -> None:
        self._lyrics = lyrics or Lyrics(language="en", words=[])

    def transcribe(self, samples: np.ndarray, sample_rate_hz: int) -> Lyrics:
        return self._lyrics

    def release(self) -> None:
        pass


def make_context(
    tmp_path: Path,
    *,
    recording_path: Path,
    reference_path: Path,
    vocal_stem_processed: bool = False,
    reference_lyrics: Lyrics | None = None,
    reference_pitch: PitchCurve | None = None,
    pitch_engine: str = "pyin",
) -> AnalysisContext:
    return AnalysisContext(
        analysis_id="test-analysis",
        user_id="test-user",
        song_id="test-song",
        recording_path=recording_path,
        reference_path=reference_path,
        work_dir=tmp_path / "work",
        song_content_hash="test-hash",
        vocal_stem_processed=vocal_stem_processed,
        reference_lyrics=reference_lyrics,
        reference_pitch=reference_pitch,
        pitch_engine=pitch_engine,  # type: ignore[arg-type]
        whisper_model="tiny",
        demucs_model="htdemucs",
        model_weights_dir=tmp_path / "weights",
    )


def _through_features(
    tmp_path: Path, recording_path: Path, reference_path: Path
) -> AnalysisContext:
    """Runs preprocess -> separate_reference (faked) -> features for real --
    the shared setup every stage 5+ test needs, since those stages now read
    MFCC/RMS/onsets out of the stage-3 shared feature cache (spec 6.9)
    instead of computing their own.
    """
    context = make_context(tmp_path, recording_path=recording_path, reference_path=reference_path)

    preprocess_result = PreprocessStage(ffmpeg_path="ffmpeg").run(context)
    context = context.with_result(preprocess_result)

    separate_result = SeparateReferenceStage(
        FakeVocalSeparator(), stem_path_for_song=lambda song_id: tmp_path / f"stem-{song_id}.wav"
    ).run(context)
    context = context.with_result(separate_result)

    features_result = FeaturesStage().run(context)
    return context.with_result(features_result)


def build_context_through_align(
    tmp_path: Path, recording_path: Path, reference_path: Path
) -> AnalysisContext:
    """Runs preprocess -> separate_reference (faked) -> features -> align for
    real, and returns the resulting context so a stage 6-13 test can start
    from it.
    """
    context = _through_features(tmp_path, recording_path, reference_path)
    align_result = AlignStage().run(context)
    return context.with_result(align_result)


def build_context_with_identity_align(
    tmp_path: Path, recording_path: Path, reference_path: Path, *, frame_count: int = 200
) -> AnalysisContext:
    """Like `build_context_through_align`, but injects a trivial identity
    warping path instead of running real DTW.

    Some tests deliberately feed the aspect stages (dynamics, timbre) two
    signals that differ exactly in what that stage measures -- which is
    also what makes them differ enough in MFCC space to legitimately fail
    real alignment first (align mixes timbre and energy information into
    the same distance metric). This decouples such a test from align's own
    threshold tuning, which is unrelated to what the test is checking.
    """
    context = _through_features(tmp_path, recording_path, reference_path)

    identity = list(range(frame_count))
    align_result = StageResult(
        stage="align",
        status=StageStatus.DONE,
        duration_ms=1,
        data={"index1": identity, "index2": identity, "hop_seconds": FEATURES_HOP_SECONDS},
    )
    return context.with_result(align_result)
