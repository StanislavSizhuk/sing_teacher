"""Audio feature DTOs shared across stages and cached on `songs` (spec 6.6, 6.7)."""

from __future__ import annotations

from pydantic import BaseModel


class LyricsWord(BaseModel):
    """One transcribed word with its timecodes, in seconds."""

    word: str
    start: float
    end: float


class Lyrics(BaseModel):
    """Whisper transcript of a song's isolated vocal stem (spec 6.3.3),
    cached in `songs.lyrics_json`."""

    language: str
    words: list[LyricsWord]


class PitchCurve(BaseModel):
    """A pitch track sampled at a fixed hop, stored compactly: frame `i`'s
    timestamp is `i * hop_seconds`, so time is never duplicated per point.
    `hz[i]` is `None` for an unvoiced/silent frame.
    """

    hop_seconds: float
    hz: list[float | None]

    def duration_seconds(self) -> float:
        return len(self.hz) * self.hop_seconds
