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


class PianoRollData(BaseModel):
    """Everything FR-31's piano-roll needs to overlay the two pitch curves
    frame-for-frame, cached in `analyses.pitch_curve_json` (spec 7).

    `reference_hz` is not the reference's own native-timeline curve --
    it is `reference_hz` resampled onto the *user's* time grid through the
    stage-4 `TimeMap`, the same lookup stage 5 already does to score
    pitch accuracy (spec 6.3.5). Without that resampling the two curves
    would only line up if the user sang at exactly the reference's tempo,
    which spec 6.3.4 explicitly does not assume. `deviation_cents[i]` is
    the signed cents difference for frame `i` (`None` wherever either side
    is unvoiced). `off_pitch[i]` is that deviation already thresholded
    against `PIANO_ROLL_OFF_PITCH_CENTS` -- the client colors a note by
    reading this flag, never by re-deriving or re-thresholding cents itself
    (spec 12.1 DRY: one source of truth per rule, not one per language).
    """

    hop_seconds: float
    user_hz: list[float | None]
    reference_hz: list[float | None]
    deviation_cents: list[float | None]
    off_pitch: list[bool]
