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
from vocalcoach.models.audio import Lyrics, PitchCurve
from vocalcoach.models.records import SongRecord
from vocalcoach.pipeline.registry import ModelRegistry
from vocalcoach.worker import build_stages

VALID_WEIGHTS = "pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10"


class _FakeSongRepository:
    def get_by_id(self, song_id: str) -> SongRecord:
        raise NotImplementedError

    def save_lyrics(self, song_id: str, lyrics: Lyrics) -> None:
        raise NotImplementedError

    def mark_vocal_stem_processed(self, song_id: str, reference_pitch: PitchCurve) -> None:
        raise NotImplementedError


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POSTGRES_DB", "vocalcoach")
    monkeypatch.setenv("POSTGRES_USER", "vocalcoach")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    monkeypatch.setenv("SCORING_WEIGHTS", VALID_WEIGHTS)
    return load_settings()


def test_every_stage_is_picklable(settings, tmp_path: Path) -> None:
    registry = ModelRegistry(
        demucs_model=settings.demucs_model,
        whisper_model=settings.whisper_model,
        pitch_engine=settings.pitch_engine,
        weights_dir=tmp_path,
    )

    stages = build_stages(settings, registry, _FakeSongRepository())

    assert len(stages) == 11
    for stage in stages:
        pickle.dumps(stage)
