"""`SongPrepJobHandler` unit tests: cold-path completion, wake-up of
waiting analyses, and FR-18's non-blocking transcription failure (T12).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vocalcoach.config import load_settings
from vocalcoach.errors import ReferenceTooQuiet
from vocalcoach.models.audio import PitchCurve
from vocalcoach.models.records import SongRecord
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.runner import RunOutcome
from vocalcoach.queue.prep_handler import SongPrepJobHandler
from vocalcoach.queue.streams import ANALYSES_STREAM_NAME


class FakeRunner:
    def __init__(self, outcome=None, error=None) -> None:
        self._outcome = outcome
        self._error = error
        self.calls: list[tuple[Any, Any, Any]] = []

    def run(self, job_id, context, already_done, progress, should_stop):
        self.calls.append((job_id, context, already_done))
        if self._error is not None:
            raise self._error
        return self._outcome


class FakeSongPrepRepo:
    def __init__(self, record: SongRecord) -> None:
        self._record = record
        self.processing_calls: list[tuple[str, str, int, int]] = []
        self.progress_calls: list[tuple[str, StageResult]] = []
        self.ready_calls: list[dict[str, Any]] = []
        self.failed_calls: list[tuple[str, str]] = []

    def get_by_id(self, song_id):
        return self._record

    def mark_prep_processing(self, song_id, first_stage, stage_index, total_stages):
        self.processing_calls.append((song_id, first_stage, stage_index, total_stages))

    def save_prep_stage_progress(self, song_id, result, next_stage, next_stage_index, total_stages):
        self.progress_calls.append((song_id, result))

    def mark_prep_ready(
        self, song_id, *, vocal_stem_path, reference_pitch, lyrics, lyrics_available
    ):
        self.ready_calls.append(
            {
                "song_id": song_id,
                "vocal_stem_path": vocal_stem_path,
                "reference_pitch": reference_pitch,
                "lyrics": lyrics,
                "lyrics_available": lyrics_available,
            }
        )

    def mark_prep_failed(self, song_id, error_code):
        self.failed_calls.append((song_id, error_code))


class FakeWakeRepo:
    def __init__(
        self,
        newly_queued: list[str] | None = None,
        positions: dict[str, int] | None = None,
        failed_ids: list[str] | None = None,
    ) -> None:
        self._newly_queued = newly_queued or []
        self._positions = positions or {}
        self._failed_ids = failed_ids or []
        self.wake_calls: list[str] = []
        self.fail_calls: list[tuple[str, str]] = []

    def wake_waiting_for_reference(self, song_id):
        self.wake_calls.append(song_id)
        return self._newly_queued, self._positions

    def fail_waiting_for_reference(self, song_id, error_code):
        self.fail_calls.append((song_id, error_code))
        return self._failed_ids


class FakeEvents:
    def __init__(self) -> None:
        self.queued: list[tuple[str, int]] = []
        self.failed: list[tuple[str, str, str]] = []

    def publish_stage(self, *args, **kwargs):
        pass

    def publish_done(self, *args, **kwargs):
        pass

    def publish_failed(self, analysis_id, error_code, message):
        self.failed.append((analysis_id, error_code, message))

    def publish_queued(self, analysis_id, position):
        self.queued.append((analysis_id, position))


class FakeRedis:
    def __init__(self) -> None:
        self.xadded: list[tuple[str, dict[str, str]]] = []

    def xadd(self, stream, fields):
        self.xadded.append((stream, fields))
        return "0-1"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("POSTGRES_DB", "vocalcoach")
    monkeypatch.setenv("POSTGRES_USER", "vocalcoach")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    monkeypatch.setenv(
        "SCORING_WEIGHTS_CLEAN",
        "pitch:0.35,rhythm:0.20,breath:0.15,dynamics:0.10,vibrato:0.10,timbre:0.10",
    )
    monkeypatch.setenv("SCORING_WEIGHTS_MIXED", "pitch:0.50,rhythm:0.30,dynamics:0.10,vibrato:0.10")
    settings = load_settings()
    settings.audio_storage_dir = tmp_path / "audio-tmp"
    settings.song_stems_dir = tmp_path / "song-stems"
    settings.audio_storage_dir.mkdir()
    return settings


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake wav")
    return path


def _pending_song(song_id: str = "s1") -> SongRecord:
    return SongRecord(id=song_id, content_hash="h", duration_sec=180, prep_status="processing")


def test_handle_success_marks_ready_with_lyrics(settings, tmp_path: Path) -> None:
    reference_pitch = PitchCurve(hop_seconds=0.01, hz=[440.0, None])
    stages = {
        "transcribe": StageResult(
            stage="transcribe",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"lyrics": {"language": "en", "words": []}},
        ),
        "prep_reference_pitch": StageResult(
            stage="prep_reference_pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"reference_pitch_curve": reference_pitch.model_dump(mode="json")},
        ),
    }
    song = SongRecord(
        id="s1", content_hash="h", duration_sec=180, prep_status="processing", prep_stages=stages
    )
    runner = FakeRunner(outcome=RunOutcome.COMPLETED)
    songs = FakeSongPrepRepo(song)
    analyses = FakeWakeRepo()
    events = FakeEvents()
    redis_client = FakeRedis()
    handler = SongPrepJobHandler(runner, songs, analyses, events, redis_client, settings)
    reference_path = _touch(settings.audio_storage_dir / "song-s1.wav")

    terminal = handler.handle("s1", should_stop=lambda: False)

    assert terminal is True
    assert len(songs.ready_calls) == 1
    assert songs.ready_calls[0]["lyrics_available"] is True
    assert songs.ready_calls[0]["lyrics"] is not None
    assert songs.ready_calls[0]["reference_pitch"] == reference_pitch
    assert songs.failed_calls == []
    assert not reference_path.exists()  # FR-43: cold path done -> raw upload deleted


def test_handle_success_with_skipped_transcribe_marks_lyrics_unavailable(
    settings, tmp_path: Path
) -> None:
    """T12/FR-18: a P3 (Whisper) failure never blocks the cold path -- the
    runner records it as SKIPPED (see test_runner.py), and this handler
    must read that as lyrics_available=false, not fail the whole prep."""
    reference_pitch = PitchCurve(hop_seconds=0.01, hz=[])
    stages = {
        "transcribe": StageResult(
            stage="transcribe",
            status=StageStatus.SKIPPED,
            duration_ms=1,
            error_code="TIMEOUT",
            error_message="whisper timed out",
        ),
        "prep_reference_pitch": StageResult(
            stage="prep_reference_pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"reference_pitch_curve": reference_pitch.model_dump(mode="json")},
        ),
    }
    song = SongRecord(
        id="s1", content_hash="h", duration_sec=180, prep_status="processing", prep_stages=stages
    )
    runner = FakeRunner(outcome=RunOutcome.COMPLETED)
    songs = FakeSongPrepRepo(song)
    handler = SongPrepJobHandler(runner, songs, FakeWakeRepo(), FakeEvents(), FakeRedis(), settings)
    _touch(settings.audio_storage_dir / "song-s1.wav")

    handler.handle("s1", should_stop=lambda: False)

    assert songs.ready_calls[0]["lyrics_available"] is False
    assert songs.ready_calls[0]["lyrics"] is None
    assert songs.failed_calls == []  # the whole prep still succeeds


def test_handle_success_wakes_waiting_analyses(settings, tmp_path: Path) -> None:
    reference_pitch = PitchCurve(hop_seconds=0.01, hz=[])
    stages = {
        "prep_reference_pitch": StageResult(
            stage="prep_reference_pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"reference_pitch_curve": reference_pitch.model_dump(mode="json")},
        ),
    }
    song = SongRecord(
        id="s1", content_hash="h", duration_sec=180, prep_status="processing", prep_stages=stages
    )
    runner = FakeRunner(outcome=RunOutcome.COMPLETED)
    analyses = FakeWakeRepo(
        newly_queued=["a1", "a2"], positions={"a1": 1, "a2": 2, "a-already-queued": 3}
    )
    events = FakeEvents()
    redis_client = FakeRedis()
    handler = SongPrepJobHandler(
        runner, FakeSongPrepRepo(song), analyses, events, redis_client, settings
    )
    _touch(settings.audio_storage_dir / "song-s1.wav")

    handler.handle("s1", should_stop=lambda: False)

    assert analyses.wake_calls == ["s1"]
    # Only the newly-woken ids get a fresh analyses:run entry -- an
    # already-queued row whose position merely shifted must never get a
    # second stream entry for the same job_id (the consumer would then see
    # it twice).
    assert redis_client.xadded == [
        (ANALYSES_STREAM_NAME, {"job_id": "a1"}),
        (ANALYSES_STREAM_NAME, {"job_id": "a2"}),
    ]
    assert set(events.queued) == {("a1", 1), ("a2", 2), ("a-already-queued", 3)}


def test_handle_failure_marks_failed_and_fails_waiting_analyses(settings, tmp_path: Path) -> None:
    song = _pending_song()
    runner = FakeRunner(error=ReferenceTooQuiet("too quiet"))
    songs = FakeSongPrepRepo(song)
    analyses = FakeWakeRepo(failed_ids=["a1", "a2"])
    events = FakeEvents()
    handler = SongPrepJobHandler(runner, songs, analyses, events, FakeRedis(), settings)
    reference_path = _touch(settings.audio_storage_dir / "song-s1.wav")

    terminal = handler.handle("s1", should_stop=lambda: False)

    assert terminal is True
    assert songs.failed_calls == [("s1", "REFERENCE_TOO_QUIET")]
    assert analyses.fail_calls == [("s1", "REFERENCE_TOO_QUIET")]
    assert set(events.failed) == {
        ("a1", "REFERENCE_TOO_QUIET", "reference preparation failed for this song"),
        ("a2", "REFERENCE_TOO_QUIET", "reference preparation failed for this song"),
    }
    # A retried prep (POST /songs/{id}/prepare) re-reads this same path.
    assert reference_path.exists()


def test_handle_failure_keeps_work_dir_for_resumable_retry(settings, tmp_path: Path) -> None:
    song = _pending_song()
    runner = FakeRunner(error=ReferenceTooQuiet("too quiet"))
    handler = SongPrepJobHandler(
        runner, FakeSongPrepRepo(song), FakeWakeRepo(), FakeEvents(), FakeRedis(), settings
    )
    _touch(settings.audio_storage_dir / "song-s1.wav")
    work_dir = settings.audio_storage_dir / "prep-s1"
    cached_reference = _touch(work_dir / "reference.wav")

    handler.handle("s1", should_stop=lambda: False)

    assert cached_reference.exists()


def test_handle_success_removes_work_dir(settings, tmp_path: Path) -> None:
    reference_pitch = PitchCurve(hop_seconds=0.01, hz=[])
    stages = {
        "prep_reference_pitch": StageResult(
            stage="prep_reference_pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"reference_pitch_curve": reference_pitch.model_dump(mode="json")},
        ),
    }
    song = SongRecord(
        id="s1", content_hash="h", duration_sec=180, prep_status="processing", prep_stages=stages
    )
    runner = FakeRunner(outcome=RunOutcome.COMPLETED)
    handler = SongPrepJobHandler(
        runner, FakeSongPrepRepo(song), FakeWakeRepo(), FakeEvents(), FakeRedis(), settings
    )
    _touch(settings.audio_storage_dir / "song-s1.wav")
    work_dir = settings.audio_storage_dir / "prep-s1"
    _touch(work_dir / "reference.wav")

    handler.handle("s1", should_stop=lambda: False)

    assert not work_dir.exists()


def test_handle_interrupted_leaves_job_non_terminal(settings) -> None:
    song = _pending_song()
    runner = FakeRunner(outcome=RunOutcome.INTERRUPTED)
    songs = FakeSongPrepRepo(song)
    handler = SongPrepJobHandler(runner, songs, FakeWakeRepo(), FakeEvents(), FakeRedis(), settings)

    terminal = handler.handle("s1", should_stop=lambda: True)

    assert terminal is False
    assert songs.ready_calls == []
    assert songs.failed_calls == []


def test_mark_permanently_failed(settings) -> None:
    analyses = FakeWakeRepo(failed_ids=["a1"])
    events = FakeEvents()
    songs = FakeSongPrepRepo(_pending_song())
    handler = SongPrepJobHandler(FakeRunner(), songs, analyses, events, FakeRedis(), settings)

    handler.mark_permanently_failed("s1")

    assert songs.failed_calls == [("s1", "INTERNAL")]
    assert analyses.fail_calls == [("s1", "INTERNAL")]
    assert events.failed == [("a1", "INTERNAL", "reference preparation failed for this song")]
