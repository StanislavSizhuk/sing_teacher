from __future__ import annotations

from pathlib import Path

import soundfile as sf

from tests.helpers import FakeTranscriber
from vocalcoach.models.audio import Lyrics, LyricsWord
from vocalcoach.models.context import SongPrepContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.stages.transcribe import TranscribeStage


def _context_with_stem(tmp_path: Path) -> SongPrepContext:
    stem_path = tmp_path / "stem.wav"
    sf.write(stem_path, [0.0] * 100, 22050)
    context = SongPrepContext(
        song_id="test-song",
        reference_path=tmp_path / "ref.wav",
        work_dir=tmp_path / "work",
        vocal_stem_path=stem_path,
    )
    return context.with_result(
        StageResult(
            stage="separate_reference",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"stem_path": str(stem_path)},
        )
    )


def test_transcribe_runs_whisper(tmp_path: Path) -> None:
    lyrics = Lyrics(language="en", words=[LyricsWord(word="la", start=0.0, end=0.2)])
    context = _context_with_stem(tmp_path)

    result = TranscribeStage(FakeTranscriber(lyrics)).run(context)

    assert result.status == StageStatus.DONE
    assert result.data["lyrics"]["words"][0]["word"] == "la"


def test_transcribe_is_optional() -> None:
    # FR-18: a P3 failure must never abort the whole cold path -- PipelineRunner
    # is what actually enforces that (see test_runner.py), this just pins
    # down the declaration it depends on.
    assert TranscribeStage(FakeTranscriber()).required is False
