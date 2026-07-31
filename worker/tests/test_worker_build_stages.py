"""Regression test: every stage `build_stages`/`build_prep_stages` wires up
must survive being pickled, since `PipelineRunner` runs each stage in a
spawn-based child process (runner.py's `_run_in_subprocess`) and
multiprocessing pickles the stage instance to hand it over. A stage holding
a closure over a local variable (a lambda, a nested `def`) fails that
pickling at run time, not at import time -- this previously crashed every
analysis that reached `separate_reference` with `AttributeError: Can't get
local object 'build_stages.<locals>.<lambda>'`.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from vocalcoach.config import load_settings
from vocalcoach.models.context import AnalysisContext
from vocalcoach.pipeline.base import ParallelGroup, PipelineStage
from vocalcoach.pipeline.registry import ModelRegistry
from vocalcoach.worker import build_prep_stages, build_stages

_WarmEntries = list[PipelineStage[AnalysisContext] | ParallelGroup[AnalysisContext]]

VALID_CLEAN_WEIGHTS = "pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10"
VALID_MIXED_WEIGHTS = "pitch:0.50,rhythm:0.30,dynamics:0.10,vibrato:0.10"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POSTGRES_DB", "vocalcoach")
    monkeypatch.setenv("POSTGRES_USER", "vocalcoach")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    monkeypatch.setenv("SCORING_WEIGHTS_CLEAN", VALID_CLEAN_WEIGHTS)
    monkeypatch.setenv("SCORING_WEIGHTS_MIXED", VALID_MIXED_WEIGHTS)
    return load_settings()


def _flatten_stage_names(entries: _WarmEntries) -> list[str]:
    names: list[str] = []
    for entry in entries:
        if isinstance(entry, ParallelGroup):
            names.extend(stage.name for stage in entry.stages)
        else:
            names.append(entry.name)
    return names


def _flatten_stages(entries: _WarmEntries) -> list[PipelineStage[AnalysisContext]]:
    stages: list[PipelineStage[AnalysisContext]] = []
    for entry in entries:
        if isinstance(entry, ParallelGroup):
            stages.extend(entry.stages)
        else:
            stages.append(entry)
    return stages


def _names_for_mode(entries: _WarmEntries, mode: str) -> list[str]:
    return [stage.name for stage in _flatten_stages(entries) if mode in stage.modes]


def test_every_warm_stage_is_picklable_and_covers_the_full_pipeline(
    settings, tmp_path: Path
) -> None:
    registry = ModelRegistry(
        demucs_model=settings.demucs_model,
        whisper_model=settings.whisper_model,
        pitch_engine=settings.pitch_engine,
        weights_dir=tmp_path,
    )

    stages = build_stages(settings, registry)

    # The 5 independent aspect stages count as one ParallelGroup entry by
    # default (spec 6.10), so top-level entries are fewer than the stages
    # they flatten to. separate_reference/transcribe are not here -- they
    # moved to the cold path (build_prep_stages, M2). `PitchStage` and
    # `MelodyPitchStage` both appear (they share the stage name "pitch",
    # ADR-0027) -- `PipelineRunner.run(mode=...)` filters to exactly one
    # per analysis.
    assert _flatten_stage_names(stages) == [
        "preprocess",
        "features",
        "align",
        "pitch",  # PitchStage (clean, A5)
        "pitch",  # MelodyPitchStage (mixed, A4)
        "key_normalization",
        "rhythm",
        "vibrato",
        "dynamics",
        "timbre",
        "breath",
        "recording_condition",
        "aggregate",
    ]
    for entry in stages:
        pickle.dumps(entry)


def test_clean_mode_excludes_melody_and_scores_all_six_aspects(settings, tmp_path: Path) -> None:
    registry = ModelRegistry(
        demucs_model=settings.demucs_model,
        whisper_model=settings.whisper_model,
        pitch_engine=settings.pitch_engine,
        weights_dir=tmp_path,
    )

    names = _names_for_mode(build_stages(settings, registry), "clean")

    assert names.count("pitch") == 1  # PitchStage only, not MelodyPitchStage too
    for aspect in ("rhythm", "vibrato", "dynamics", "timbre", "breath"):
        assert aspect in names


def test_mixed_mode_excludes_timbre_and_breath(settings, tmp_path: Path) -> None:
    registry = ModelRegistry(
        demucs_model=settings.demucs_model,
        whisper_model=settings.whisper_model,
        pitch_engine=settings.pitch_engine,
        weights_dir=tmp_path,
    )

    names = _names_for_mode(build_stages(settings, registry), "mixed")

    assert names.count("pitch") == 1  # MelodyPitchStage only, not PitchStage too
    assert "timbre" not in names
    assert "breath" not in names
    for aspect in ("rhythm", "vibrato", "dynamics"):
        assert aspect in names


def test_pipeline_parallel_aspects_false_keeps_stages_flat(
    settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPELINE_PARALLEL_ASPECTS", "false")
    settings = load_settings()
    registry = ModelRegistry(
        demucs_model=settings.demucs_model,
        whisper_model=settings.whisper_model,
        pitch_engine=settings.pitch_engine,
        weights_dir=tmp_path,
    )

    stages = build_stages(settings, registry)

    assert not any(isinstance(entry, ParallelGroup) for entry in stages)
    assert len(stages) == 13


def test_every_prep_stage_is_picklable_and_covers_the_full_cold_path(
    settings, tmp_path: Path
) -> None:
    registry = ModelRegistry(
        demucs_model=settings.demucs_model,
        whisper_model=settings.whisper_model,
        pitch_engine=settings.pitch_engine,
        weights_dir=tmp_path,
    )

    stages = build_prep_stages(settings, registry)

    assert [stage.name for stage in stages] == [
        "prep_reference",
        "separate_reference",
        "transcribe",
        "prep_reference_pitch",
    ]
    for stage in stages:
        pickle.dumps(stage)
