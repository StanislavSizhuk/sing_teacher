"""T13 (spec 15.2): the parallel aspect-stage execution (spec 6.10) must
produce the same scores as running the same stages sequentially -- the
whole point of `PIPELINE_PARALLEL_ASPECTS=false` existing as a fallback is
that it is a *behavioral* no-op, only a performance one.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import sine_wave
from tests.helpers import canonical_stem_path, reference_pitch_curve_for
from vocalcoach.config import ScoringWeights
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.mode import Mode
from vocalcoach.pipeline.base import ParallelGroup
from vocalcoach.pipeline.registry import PyinPitchDetector
from vocalcoach.pipeline.runner import PipelineRunner, RunOutcome
from vocalcoach.pipeline.stages.aggregate import AggregateStage
from vocalcoach.pipeline.stages.align import AlignStage
from vocalcoach.pipeline.stages.breath import BreathStage
from vocalcoach.pipeline.stages.dynamics import DynamicsStage
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.key_normalization import KeyNormalizationStage
from vocalcoach.pipeline.stages.pitch import PitchStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.recording_condition import RecordingConditionStage
from vocalcoach.pipeline.stages.rhythm import RhythmStage
from vocalcoach.pipeline.stages.timbre import TimbreStage
from vocalcoach.pipeline.stages.vibrato import VibratoStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

_WEIGHTS: dict[Mode, ScoringWeights] = {
    "clean": ScoringWeights.parse(
        "pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10", "clean"
    ),
    "mixed": ScoringWeights.parse("pitch:0.50,rhythm:0.30,dynamics:0.10,vibrato:0.10", "mixed"),
}
_SCORED_ASPECTS = ("pitch", "rhythm", "vibrato", "dynamics", "timbre", "breath")
# `clean`'s defaults (spec 20.5): this test never enables transposition, so
# a shift is never eligible to apply regardless of these exact values.
_KEY_SHIFT_MIN_SEMITONES = 0.6
_KEY_SHIFT_MAX_IQR = 0.5
_MAX_KEY_SHIFT_SEMITONES = 7.0


class _NoOpProgress:
    def mark_processing(self, *args: Any, **kwargs: Any) -> None:
        pass

    def save_stage_progress(self, *args: Any, **kwargs: Any) -> None:
        pass


class _CapturingProgress(_NoOpProgress):
    def __init__(self) -> None:
        self.results: dict[str, Any] = {}

    def save_stage_progress(self, result, next_stage, next_stage_index, total_stages) -> None:
        self.results[result.stage] = result


class _NoOpEvents:
    def publish_stage(self, *args: Any, **kwargs: Any) -> None:
        pass

    def publish_done(self, *args: Any, **kwargs: Any) -> None:
        pass

    def publish_failed(self, *args: Any, **kwargs: Any) -> None:
        pass

    def publish_queued(self, *args: Any, **kwargs: Any) -> None:
        pass


def _build_stages(*, parallel: bool):
    aspect_stages = (RhythmStage(), VibratoStage(), DynamicsStage(), TimbreStage(), BreathStage())
    aspects = [ParallelGroup(aspect_stages)] if parallel else list(aspect_stages)
    return [
        PreprocessStage(ffmpeg_path="ffmpeg"),
        FeaturesStage(),
        AlignStage(PyinPitchDetector()),
        PitchStage(),
        KeyNormalizationStage(
            _KEY_SHIFT_MIN_SEMITONES, _KEY_SHIFT_MAX_IQR, _MAX_KEY_SHIFT_SEMITONES
        ),
        *aspects,
        RecordingConditionStage(accompaniment_detect_threshold=0.15),
        AggregateStage(_WEIGHTS, "test"),
    ]


def _run_and_get_scores(
    tmp_path: Path,
    recording: Path,
    reference: Path,
    reference_pitch,
    *,
    parallel: bool,
    suffix: str,
) -> dict[str, float]:
    context = AnalysisContext(
        analysis_id=f"a-{suffix}",
        user_id="u",
        song_id=f"s-{suffix}",
        recording_path=recording,
        work_dir=tmp_path / f"work-{suffix}",
        reference_vocal_stem_path=canonical_stem_path(tmp_path, reference),
        reference_pitch=reference_pitch,
    )
    stages = _build_stages(parallel=parallel)
    progress = _CapturingProgress()
    runner = PipelineRunner(stages, _NoOpEvents())

    outcome = runner.run(f"a-{suffix}", context, {}, progress)
    assert outcome == RunOutcome.COMPLETED

    scores = {aspect: float(progress.results[aspect].data["score"]) for aspect in _SCORED_ASPECTS}
    scores["overall"] = float(progress.results["aggregate"].data["overall_score"])
    return scores


def test_parallel_and_sequential_execution_produce_matching_scores(
    tmp_path: Path, wav_writer
) -> None:
    signal = sine_wave(4.0, 44100, 300.0, vibrato_hz=5.0, vibrato_cents=40.0)
    recording = wav_writer("recording.wav", signal, 44100)
    reference = wav_writer("reference.wav", signal, 44100)
    reference_pitch = reference_pitch_curve_for(tmp_path, reference)

    parallel_scores = _run_and_get_scores(
        tmp_path, recording, reference, reference_pitch, parallel=True, suffix="parallel"
    )
    sequential_scores = _run_and_get_scores(
        tmp_path, recording, reference, reference_pitch, parallel=False, suffix="sequential"
    )

    for aspect, parallel_score in parallel_scores.items():
        assert parallel_score == pytest.approx(sequential_scores[aspect], abs=1.0), aspect
