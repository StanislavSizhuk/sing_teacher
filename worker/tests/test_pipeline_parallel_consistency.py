"""T13 (spec 15.2): the parallel aspect-stage execution (spec 6.10) must
produce the same scores as running the same stages sequentially -- the
whole point of `PIPELINE_PARALLEL_ASPECTS=false` existing as a fallback is
that it is a *behavioral* no-op, only a performance one.
"""

from __future__ import annotations

import functools
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import sine_wave
from tests.helpers import FakeVocalSeparator
from vocalcoach.config import ScoringWeights
from vocalcoach.models.context import AnalysisContext
from vocalcoach.pipeline.base import ParallelGroup
from vocalcoach.pipeline.registry import PyinPitchDetector
from vocalcoach.pipeline.runner import PipelineRunner, RunOutcome
from vocalcoach.pipeline.stages.aggregate import AggregateStage
from vocalcoach.pipeline.stages.align import AlignStage
from vocalcoach.pipeline.stages.breath import BreathStage
from vocalcoach.pipeline.stages.dynamics import DynamicsStage
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.pitch import PitchStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.recording_condition import RecordingConditionStage
from vocalcoach.pipeline.stages.rhythm import RhythmStage
from vocalcoach.pipeline.stages.separate_reference import SeparateReferenceStage
from vocalcoach.pipeline.stages.timbre import TimbreStage
from vocalcoach.pipeline.stages.vibrato import VibratoStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

_WEIGHTS = ScoringWeights.parse(
    "pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10"
)
_SCORED_ASPECTS = ("pitch", "rhythm", "vibrato", "dynamics", "timbre", "breath")


class _NoOpAnalysisRepo:
    def mark_processing(self, *args: Any, **kwargs: Any) -> None:
        pass

    def save_stage_progress(self, *args: Any, **kwargs: Any) -> None:
        pass


class _CapturingAnalysisRepo(_NoOpAnalysisRepo):
    def __init__(self) -> None:
        self.results: dict[str, Any] = {}

    def save_stage_progress(
        self, analysis_id, result, next_stage, next_stage_index, total_stages
    ) -> None:
        self.results[result.stage] = result


class _NoOpEvents:
    def publish_stage(self, *args: Any, **kwargs: Any) -> None:
        pass

    def publish_done(self, *args: Any, **kwargs: Any) -> None:
        pass

    def publish_failed(self, *args: Any, **kwargs: Any) -> None:
        pass


def _stem_path(tmp_path: Path, suffix: str, song_id: str) -> Path:
    return tmp_path / f"stem-{song_id}-{suffix}.wav"


def _build_stages(*, parallel: bool, stem_path_for_song: Callable[[str], Path]):
    aspect_stages = (RhythmStage(), VibratoStage(), DynamicsStage(), TimbreStage(), BreathStage())
    aspects = [ParallelGroup(aspect_stages)] if parallel else list(aspect_stages)
    return [
        PreprocessStage(ffmpeg_path="ffmpeg"),
        SeparateReferenceStage(FakeVocalSeparator(), stem_path_for_song=stem_path_for_song),
        FeaturesStage(),
        AlignStage(),
        PitchStage(PyinPitchDetector()),
        *aspects,
        RecordingConditionStage(),
        AggregateStage(_WEIGHTS, "test"),
    ]


def _run_and_get_scores(
    tmp_path: Path, recording: Path, reference: Path, *, parallel: bool, suffix: str
) -> dict[str, float]:
    context = AnalysisContext(
        analysis_id=f"a-{suffix}",
        user_id="u",
        song_id=f"s-{suffix}",
        recording_path=recording,
        reference_path=reference,
        work_dir=tmp_path / f"work-{suffix}",
        song_content_hash="hash",
        vocal_stem_processed=False,
        pitch_engine="pyin",
        whisper_model="tiny",
        demucs_model="htdemucs",
        model_weights_dir=tmp_path / "weights",
    )
    stages = _build_stages(
        parallel=parallel,
        stem_path_for_song=functools.partial(_stem_path, tmp_path, suffix),
    )
    repo = _CapturingAnalysisRepo()
    runner = PipelineRunner(stages, repo, _NoOpEvents())

    outcome = runner.run(f"a-{suffix}", context, already_done={})
    assert outcome == RunOutcome.COMPLETED

    scores = {aspect: float(repo.results[aspect].data["score"]) for aspect in _SCORED_ASPECTS}
    scores["overall"] = float(repo.results["aggregate"].data["overall_score"])
    return scores


def test_parallel_and_sequential_execution_produce_matching_scores(
    tmp_path: Path, wav_writer
) -> None:
    signal = sine_wave(4.0, 44100, 300.0, vibrato_hz=5.0, vibrato_cents=40.0)
    recording = wav_writer("recording.wav", signal, 44100)
    reference = wav_writer("reference.wav", signal, 44100)

    parallel_scores = _run_and_get_scores(
        tmp_path, recording, reference, parallel=True, suffix="parallel"
    )
    sequential_scores = _run_and_get_scores(
        tmp_path, recording, reference, parallel=False, suffix="sequential"
    )

    for aspect, parallel_score in parallel_scores.items():
        assert parallel_score == pytest.approx(sequential_scores[aspect], abs=1.0), aspect
