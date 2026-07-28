"""DTOs the repositories read/write -- the worker's view of the `songs` and
`analyses` rows the Go API also owns (spec 7).
"""

from __future__ import annotations

from pydantic import BaseModel

from vocalcoach.models.audio import Lyrics, PitchCurve
from vocalcoach.models.results import StageResult


class SongRecord(BaseModel):
    id: str
    content_hash: str
    duration_sec: int
    vocal_stem_processed: bool
    lyrics: Lyrics | None = None
    reference_pitch: PitchCurve | None = None


class AnalysisRecord(BaseModel):
    id: str
    user_id: str
    song_id: str
    status: str
    stages: dict[str, StageResult]
