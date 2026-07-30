from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vocalcoach.config import load_settings
from vocalcoach.errors import NoVoiceDetected
from vocalcoach.models.audio import Lyrics, LyricsWord, PianoRollData, PitchCurve
from vocalcoach.models.records import AnalysisRecord, SongRecord
from vocalcoach.models.results import StageResult, StageStatus
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
        self.saved_scores: list[tuple[str, str, float]] = []
        self.saved_piano_rolls: list[tuple[str, PianoRollData]] = []
        self.saved_scoring_results: list[tuple[str, float, str, str]] = []
        self.progress_snapshots: list[tuple[str, str, float]] = []

    def get_by_id(self, analysis_id):
        return self._record

    def save_aspect_score(self, analysis_id, aspect, score):
        self.saved_scores.append((analysis_id, aspect, score))

    def save_piano_roll(self, analysis_id, data):
        self.saved_piano_rolls.append((analysis_id, data))

    def save_scoring_result(self, analysis_id, overall_score, feedback_text, scoring_version):
        self.saved_scoring_results.append(
            (analysis_id, overall_score, feedback_text, scoring_version)
        )

    def record_progress_snapshot(self, analysis_id, user_id, overall_score):
        self.progress_snapshots.append((analysis_id, user_id, overall_score))

    def mark_done(self, analysis_id, model_versions):
        self.marked_done.append((analysis_id, model_versions))

    def mark_failed(self, analysis_id, error_code):
        self.marked_failed.append((analysis_id, error_code))


class FakeSongRepo:
    def __init__(self, record: SongRecord) -> None:
        self._record = record
        self.saved_lyrics: list[tuple[str, Any]] = []
        self.saved_pitch_curves: list[tuple[str, Any]] = []

    def get_by_id(self, song_id):
        return self._record

    def save_lyrics(self, song_id, lyrics) -> None:
        self.saved_lyrics.append((song_id, lyrics))

    def mark_vocal_stem_processed(self, song_id, reference_pitch) -> None:
        self.saved_pitch_curves.append((song_id, reference_pitch))


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
    assert analyses.progress_snapshots == []  # no "aggregate" stage in this fixture's stages={}
    assert events.done == ["a1"]
    assert not recording_path.exists()  # FR-43: done -> recording deleted now
    assert not song_path.exists()  # already cached -> original upload no longer needed


def test_handle_success_denormalizes_scores_piano_roll_and_aggregate(
    settings, tmp_path: Path
) -> None:
    piano_roll = PianoRollData(
        hop_seconds=0.01,
        user_hz=[440.0, None, 441.5],
        reference_hz=[440.0, None, 440.0],
        deviation_cents=[0.0, None, 5.9],
        off_pitch=[False, False, False],
    )
    stages = {
        "pitch": StageResult(
            stage="pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"score": 87.5, "piano_roll": piano_roll.model_dump(mode="json")},
        ),
        "rhythm": StageResult(
            stage="rhythm", status=StageStatus.DONE, duration_ms=1, data={"score": 91.0}
        ),
        "aggregate": StageResult(
            stage="aggregate",
            status=StageStatus.DONE,
            duration_ms=1,
            data={
                "overall_score": 88.4,
                "feedback_text": "Overall score: 88/100.",
                "scoring_version": "1.0",
                "aspect_scores": {"pitch": 87.5, "rhythm": 91.0},
            },
        ),
        # Not every stage necessarily carries a "score" key -- must not crash on one that doesn't.
        "align": StageResult(stage="align", status=StageStatus.DONE, duration_ms=1, data={}),
    }
    analysis = AnalysisRecord(
        id="a5", user_id="u1", song_id="s1", status="processing", stages=stages
    )
    song = SongRecord(id="s1", content_hash="h", duration_sec=180, vocal_stem_processed=True)
    runner = FakeRunner(outcome=RunOutcome.COMPLETED)
    analyses = FakeAnalysisRepo(analysis)
    handler = AnalysisJobHandler(runner, analyses, FakeSongRepo(song), FakeEvents(), settings, {})
    _touch(settings.audio_storage_dir / "analysis-a5.wav")
    _touch(settings.audio_storage_dir / "song-s1.wav")

    handler.handle("a5", should_stop=lambda: False)

    assert ("a5", "pitch", 87.5) in analyses.saved_scores
    assert ("a5", "rhythm", 91.0) in analyses.saved_scores
    assert len(analyses.saved_scores) == 2  # "align" has no "score" key, nothing else does either
    assert analyses.saved_piano_rolls == [("a5", piano_roll)]
    assert analyses.saved_scoring_results == [("a5", 88.4, "Overall score: 88/100.", "1.0")]
    assert analyses.progress_snapshots == [("a5", "u1", 88.4)]
    # Score persistence must happen before mark_done, not after -- a reader
    # that sees status="done" should already find every score in place.
    assert analyses.marked_done == [("a5", {})]


def test_handle_success_persists_song_cache_on_cache_miss(settings, tmp_path: Path) -> None:
    """transcribe/pitch cannot write songs.lyrics_json/reference_pitch_json
    themselves (their StageResult crosses PipelineRunner's spawn boundary,
    which a live DB connection can't survive) -- the handler must do it
    from their StageResults once the pipeline finishes (spec 6.6)."""
    lyrics = Lyrics(language="en", words=[LyricsWord(word="la", start=0.0, end=0.2)])
    reference_pitch = PitchCurve(hop_seconds=0.01, hz=[440.0, None])
    stages = {
        "transcribe": StageResult(
            stage="transcribe",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"lyrics": lyrics.model_dump(mode="json"), "cached": False},
        ),
        "pitch": StageResult(
            stage="pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={
                "reference_pitch_curve": reference_pitch.model_dump(mode="json"),
                "reference_cached": False,
            },
        ),
    }
    analysis = AnalysisRecord(
        id="a6", user_id="u1", song_id="s1", status="processing", stages=stages
    )
    song = SongRecord(id="s1", content_hash="h", duration_sec=180, vocal_stem_processed=False)
    runner = FakeRunner(outcome=RunOutcome.COMPLETED)
    songs = FakeSongRepo(song)
    handler = AnalysisJobHandler(
        runner, FakeAnalysisRepo(analysis), songs, FakeEvents(), settings, {}
    )
    _touch(settings.audio_storage_dir / "analysis-a6.wav")
    _touch(settings.audio_storage_dir / "song-s1.wav")

    handler.handle("a6", should_stop=lambda: False)

    assert songs.saved_lyrics == [("s1", lyrics)]
    assert songs.saved_pitch_curves == [("s1", reference_pitch)]


def test_handle_success_skips_song_cache_write_already_served_from_cache(
    settings, tmp_path: Path
) -> None:
    """A song already warm for this analysis (both stages answered from
    cache) must not be re-written with the same values on every analysis
    of it."""
    stages = {
        "transcribe": StageResult(
            stage="transcribe",
            status=StageStatus.DONE,
            duration_ms=1,
            data={
                "lyrics": Lyrics(language="en", words=[]).model_dump(mode="json"),
                "cached": True,
            },
        ),
        "pitch": StageResult(
            stage="pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={
                "reference_pitch_curve": PitchCurve(hop_seconds=0.01, hz=[]).model_dump(
                    mode="json"
                ),
                "reference_cached": True,
            },
        ),
    }
    analysis = AnalysisRecord(
        id="a9", user_id="u1", song_id="s1", status="processing", stages=stages
    )
    song = SongRecord(id="s1", content_hash="h", duration_sec=180, vocal_stem_processed=True)
    runner = FakeRunner(outcome=RunOutcome.COMPLETED)
    songs = FakeSongRepo(song)
    handler = AnalysisJobHandler(
        runner, FakeAnalysisRepo(analysis), songs, FakeEvents(), settings, {}
    )
    _touch(settings.audio_storage_dir / "analysis-a9.wav")
    _touch(settings.audio_storage_dir / "song-s1.wav")

    handler.handle("a9", should_stop=lambda: False)

    assert songs.saved_lyrics == []
    assert songs.saved_pitch_curves == []


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


def test_handle_failure_keeps_work_dir_for_resumable_retry(settings, tmp_path: Path) -> None:
    """A retry resumes from stages already in stages_json (spec 6.8) --
    here, preprocess -- by trusting the file paths its cached StageResult
    recorded, all inside work_dir. Deleting work_dir on failure would make
    the very next retry crash trying to reopen a file this run just
    removed, which is exactly what happened before this test existed."""
    stages = {
        "preprocess": StageResult(
            stage="preprocess",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"reference_path": "reference.wav", "recording_path": "recording.wav"},
        )
    }
    analysis = AnalysisRecord(
        id="a7", user_id="u1", song_id="s1", status="processing", stages=stages
    )
    song = SongRecord(id="s1", content_hash="h", duration_sec=180, vocal_stem_processed=False)
    runner = FakeRunner(error=NoVoiceDetected("no voice"))
    handler = AnalysisJobHandler(
        runner, FakeAnalysisRepo(analysis), FakeSongRepo(song), FakeEvents(), settings, {}
    )
    _touch(settings.audio_storage_dir / "analysis-a7.wav")
    _touch(settings.audio_storage_dir / "song-s1.wav")
    work_dir = settings.audio_storage_dir / "work-a7"
    cached_reference = _touch(work_dir / "reference.wav")

    handler.handle("a7", should_stop=lambda: False)

    assert cached_reference.exists()


def test_handle_success_removes_work_dir(settings, tmp_path: Path) -> None:
    analysis = AnalysisRecord(id="a8", user_id="u1", song_id="s1", status="processing", stages={})
    song = SongRecord(id="s1", content_hash="h", duration_sec=180, vocal_stem_processed=True)
    runner = FakeRunner(outcome=RunOutcome.COMPLETED)
    handler = AnalysisJobHandler(
        runner, FakeAnalysisRepo(analysis), FakeSongRepo(song), FakeEvents(), settings, {}
    )
    _touch(settings.audio_storage_dir / "analysis-a8.wav")
    _touch(settings.audio_storage_dir / "song-s1.wav")
    work_dir = settings.audio_storage_dir / "work-a8"
    _touch(work_dir / "reference.wav")

    handler.handle("a8", should_stop=lambda: False)

    assert not work_dir.exists()


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
