"""Regression test: every stage `build_stages` wires up must survive being
pickled, since `PipelineRunner` runs each stage in a spawn-based child
process (runner.py's `_run_in_subprocess`) and multiprocessing pickles the
stage instance to hand it over. A stage holding a closure over a local
variable (a lambda, a nested `def`) fails that pickling at run time, not at
import time -- this previously crashed every analysis that reached
`separate_reference` with `AttributeError: Can't get local object
'build_stages.<locals>.<lambda>'`.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from vocalcoach.config import load_settings
from vocalcoach.pipeline.base import ParallelGroup
from vocalcoach.pipeline.registry import ModelRegistry
from vocalcoach.worker import build_stages

VALID_WEIGHTS = "pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POSTGRES_DB", "vocalcoach")
    monkeypatch.setenv("POSTGRES_USER", "vocalcoach")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    monkeypatch.setenv("SCORING_WEIGHTS", VALID_WEIGHTS)
    return load_settings()


def _flatten_stage_names(entries) -> list[str]:
    names: list[str] = []
    for entry in entries:
        if isinstance(entry, ParallelGroup):
            names.extend(stage.name for stage in entry.stages)
        else:
            names.append(entry.name)
    return names


def test_every_stage_is_picklable_and_covers_the_full_pipeline(settings, tmp_path: Path) -> None:
    registry = ModelRegistry(
        demucs_model=settings.demucs_model,
        whisper_model=settings.whisper_model,
        pitch_engine=settings.pitch_engine,
        weights_dir=tmp_path,
    )

    stages = build_stages(settings, registry)

    # The 5 independent aspect stages count as one ParallelGroup entry by
    # default (spec 6.10), so top-level entries (9) are fewer than the 13
    # actual stages they flatten to.
    assert len(stages) == 9
    assert _flatten_stage_names(stages) == [
        "preprocess",
        "separate_reference",
        "features",
        "transcribe",
        "align",
        "pitch",
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
