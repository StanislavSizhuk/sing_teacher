"""DTOs the repositories read/write -- the worker's view of the `songs` and
`analyses` rows the Go API also owns (spec 7).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from vocalcoach.models.audio import Lyrics, PitchCurve
from vocalcoach.models.mode import Mode
from vocalcoach.models.results import StageResult


class SongRecord(BaseModel):
    """Used both by the warm path (`AnalysisJobHandler`, which only ever
    reads `vocal_stem_path`/`reference_pitch`/`lyrics` once `prep_status`
    is `ready`, spec 6.6) and the cold path (`SongPrepJobHandler`, which
    also needs `prep_status`/`prep_stages` for resumability, spec 6.1/6.8).
    """

    id: str
    content_hash: str
    duration_sec: int
    prep_status: str
    vocal_stem_path: Path | None = None
    reference_pitch: PitchCurve | None = None
    lyrics: Lyrics | None = None
    lyrics_available: bool = False
    #: Cold-path stage results already durably saved (spec 6.1, 6.8): a
    #: retried prep job resumes from the first unfinished P-stage, mirroring
    #: `AnalysisRecord.stages`.
    prep_stages: dict[str, StageResult] = Field(default_factory=dict)


class AnalysisRecord(BaseModel):
    id: str
    user_id: str
    song_id: str
    status: str
    stages: dict[str, StageResult]
    #: FR-27: what the Go API stored from the user's own choice at
    #: POST /analyses. Defaults to "clean" so every existing test/call site
    #: built before M4 wired this column keeps behaving exactly as before
    #: (same default AnalysisContext itself already used).
    mode: Mode = "clean"
    #: FR-31, spec 6.8. Same mode-dependent default rule as
    #: `AnalysisContext.allow_transposition` -- the Go API decides the
    #: actual default when a request omits it (spec 8.3), this field just
    #: carries whatever was stored.
    allow_transposition: bool = False
