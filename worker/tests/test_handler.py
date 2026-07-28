from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vocalcoach.config import load_settings
from vocalcoach.errors import NoVoiceDetected
from vocalcoach.models.records import AnalysisRecord, SongRecord
from vocalcoach.pipeline.runner import RunOutcome
from vocalcoach.queue.handler import AnalysisJobHandler


class FakeRunner:
    def __init__(self, outcome=None, error=None) -> None:
        self._outcome = outcome
        self._error = error
        self.calls: list[tuple[Any, Any, Any]] = []

    def run(self, analysis_id, context, already_done, should_stop):
        self.calls.append((analysis_id, context, already_done))
        if self._error is not None:
            raise self._error
        return self._outcome


class FakeAnalysisRepo:
    def __init__(self, record: AnalysisRecord) -> None:
        self._record = record
        self.marked_done: list[tuple[str, dict[str, Any]]] = []
        self.marked_failed: list[tuple[str, str]] = []

    def get_by_id(self, analysis_id):
        return self._record

    def mark_done(self, analysis_id, model_versions):
        self.marked_done.append((analysis_id, model_versions))

    def mark_failed(self, analysis_id, error_code):
        self.marked_failed.append((analysis_id, error_code))


class FakeSongRepo:
    def __init__(self, record: SongRecord) -> None:
        self._record = record

    def get_by_id(self, song_id):
        return self._record


class FakeEvents:
    def __init__(self) -> None:
        self.done: list[str] = []
        self.failed: list[tuple[str, str, str]] = []

    def publish_stage(self, analysis_id, name, index, total):
        pass

    def publish_done(self, analysis_id):
        self.done.append(analysis_id)

    def publish_failed(self, analysis_id, error_code, message):
        self.failed.append((analysis_id, error_code, message))


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("POSTGRES_DB", "vocalcoach")
    monkeypatch.setenv("POSTGRES_USER", "vocalcoach")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    monkeypatch.setenv(
        "SCORING_WEIGHTS",
        "pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10",
    )
    settings = load_settings()
    settings.audio_storage_dir = tmp_path / "audio-tmp"
    settings.song_stems_dir = tmp_path / "song-stems"
    settings.audio_storage_dir.mkdir()
    return settings


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake wav")
    return path


def test_handle_success_deletes_recording_and_cached_song_source(settings, tmp_path: Path) -> None:
    analysis = AnalysisRecord(id="a1", user_id="u1", song_id="s1", status="processing", stages={})
    song = SongRecord(id="s1", content_hash="h", duration_sec=180, vocal_stem_processed=True)
    runner = FakeRunner(outcome=RunOutcome.COMPLETED)
    analyses = FakeAnalysisRepo(analysis)
    songs = FakeSongRepo(song)
    events = FakeEvents()
    handler = AnalysisJobHandler(runner, analyses, songs, events, settings, {"demucs": "htdemucs"})

    recording_path = _touch(settings.audio_storage_dir / "analysis-a1.wav")
    song_path = _touch(settings.audio_storage_dir / "song-s1.wav")

    terminal = handler.handle("a1", should_stop=lambda: False)

    assert terminal is True
    assert analyses.marked_done == [("a1", {"demucs": "htdemucs"})]
    assert events.done == ["a1"]
    assert not recording_path.exists()  # FR-43: done -> recording deleted now
    assert not song_path.exists()  # already cached -> original upload no longer needed


def test_handle_failure_keeps_recording_for_retry(settings, tmp_path: Path) -> None:
    analysis = AnalysisRecord(id="a2", user_id="u1", song_id="s1", status="processing", stages={})
    song = SongRecord(id="s1", content_hash="h", duration_sec=180, vocal_stem_processed=False)
    runner = FakeRunner(error=NoVoiceDetected("no voice"))
    analyses = FakeAnalysisRepo(analysis)
    songs = FakeSongRepo(song)
    events = FakeEvents()
    handler = AnalysisJobHandler(runner, analyses, songs, events, settings, {})

    recording_path = _touch(settings.audio_storage_dir / "analysis-a2.wav")
    song_path = _touch(settings.audio_storage_dir / "song-s1.wav")

    terminal = handler.handle("a2", should_stop=lambda: False)

    assert terminal is True
    assert analyses.marked_failed == [("a2", "NO_VOICE_DETECTED")]
    assert events.failed == [("a2", "NO_VOICE_DETECTED", "no voice")]
    # A failed (possibly retryable) analysis must not lose its recording
    # (service/analysis/retry.go assumes it stays on disk untouched).
    assert recording_path.exists()
    # Cache never warmed for this song -- original upload still needed.
    assert song_path.exists()


def test_handle_interrupted_leaves_job_non_terminal(settings) -> None:
    analysis = AnalysisRecord(id="a3", user_id="u1", song_id="s1", status="processing", stages={})
    song = SongRecord(id="s1", content_hash="h", duration_sec=180, vocal_stem_processed=False)
    runner = FakeRunner(outcome=RunOutcome.INTERRUPTED)
    analyses = FakeAnalysisRepo(analysis)
    events = FakeEvents()
    handler = AnalysisJobHandler(runner, analyses, FakeSongRepo(song), events, settings, {})

    terminal = handler.handle("a3", should_stop=lambda: True)

    assert terminal is False
    assert analyses.marked_done == []
    assert analyses.marked_failed == []
    assert events.done == []
    assert events.failed == []
