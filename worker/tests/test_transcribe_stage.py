from __future__ import annotations

from pathlib import Path

from tests.helpers import FakeSongRepository, FakeTranscriber, make_context
from vocalcoach.models.audio import Lyrics, LyricsWord
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.stages.transcribe import TranscribeStage


def test_transcribe_runs_and_saves_lyrics_on_cache_miss(tmp_path: Path) -> None:
    lyrics = Lyrics(language="en", words=[LyricsWord(word="la", start=0.0, end=0.2)])
    songs = FakeSongRepository()
    context = make_context(
        tmp_path, recording_path=tmp_path / "rec.wav", reference_path=tmp_path / "ref.wav"
    )
    stem_path = tmp_path / "stem.wav"
    import soundfile as sf

    sf.write(stem_path, [0.0] * 100, 22050)
    context = context.with_result(
        StageResult(
            stage="separate_reference",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"stem_path": str(stem_path)},
        )
    )

    result = TranscribeStage(FakeTranscriber(lyrics), songs).run(context)

    assert result.data["cached"] is False
    assert songs.saved_lyrics == lyrics
    assert result.data["lyrics"]["words"][0]["word"] == "la"


def test_transcribe_skips_whisper_when_cached(tmp_path: Path) -> None:
    lyrics = Lyrics(language="en", words=[LyricsWord(word="cached", start=0.0, end=0.1)])
    songs = FakeSongRepository()
    context = make_context(
        tmp_path,
        recording_path=tmp_path / "rec.wav",
        reference_path=tmp_path / "ref.wav",
        vocal_stem_processed=True,
        reference_lyrics=lyrics,
    )

    class ExplodingTranscriber:
        def transcribe(self, samples, sample_rate_hz):
            raise AssertionError("must not be called on a cache hit")

        def release(self) -> None:
            pass

    result = TranscribeStage(ExplodingTranscriber(), songs).run(context)

    assert result.data["cached"] is True
    assert result.data["lyrics"]["words"][0]["word"] == "cached"
    assert songs.saved_lyrics is None
