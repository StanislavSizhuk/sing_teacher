"""The contexts pipeline stages receive: `AnalysisContext` for the warm path
(spec 6.5, A1-A10) and `SongPrepContext` for the cold path (spec 6.4,
P1-P4). Both are passed by value into a stage's own child process (spec
6.5), so everything they hold must be picklable -- pydantic models are.
Both share the same `result`/`with_result` shape (`pipeline.base.PipelineContext`)
so `PipelineRunner` can drive either without caring which.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from vocalcoach.models.audio import Lyrics, PitchCurve
from vocalcoach.models.mode import Mode
from vocalcoach.models.results import StageResult


class AnalysisContext(BaseModel):
    """Warm path (spec 6.5): only ever built once its song's cold path has
    reached `ready`, so the reference vocal stem and pitch curve are always
    already cached here -- this context never carries the raw reference
    upload, and no warm-path stage re-runs Demucs/Whisper/reference-pitch
    detection (spec 6.6, 6.13). `reference_lyrics` alone stays optional:
    transcription (P3) is itself an optional cold-path stage (FR-18).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    analysis_id: str
    user_id: str
    song_id: str

    recording_path: Path
    work_dir: Path

    reference_vocal_stem_path: Path
    reference_pitch: PitchCurve
    reference_lyrics: Lyrics | None = None

    #: FR-27 default: a cappella is the recommended, default choice (spec
    #: 2.3) -- defaulting here too means every existing test/call site that
    #: never cared about mode keeps behaving exactly as before.
    mode: Mode = "clean"
    #: FR-31: off by default in `clean` (nowhere to transpose to when
    #: singing straight over the reference in headphones), on by default in
    #: `mixed` -- that mode-dependent default is chosen by whoever builds
    #: this context (the queue handler), not by this model itself.
    allow_transposition: bool = False

    completed: dict[str, StageResult] = Field(default_factory=dict)

    def result(self, stage_name: str) -> StageResult:
        found = self.completed.get(stage_name)
        if found is None:
            raise KeyError(f"stage '{stage_name}' has not completed yet")
        return found

    def with_result(self, result: StageResult) -> Self:
        """Returns a copy of this context with `result` recorded, used by
        the runner to build the context for the next stage without mutating
        the one the just-finished stage's child process pickled back."""
        return self.model_copy(update={"completed": {**self.completed, result.stage: result}})


class SongPrepContext(BaseModel):
    """Cold path (spec 6.4): builds a song's reference cache exactly once.
    `vocal_stem_path` is where P2 (Demucs) must write the isolated vocal --
    a deterministic, id-derived path (spec 11.3), not chosen by the stage.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    song_id: str

    reference_path: Path
    work_dir: Path
    vocal_stem_path: Path

    completed: dict[str, StageResult] = Field(default_factory=dict)

    def result(self, stage_name: str) -> StageResult:
        found = self.completed.get(stage_name)
        if found is None:
            raise KeyError(f"stage '{stage_name}' has not completed yet")
        return found

    def with_result(self, result: StageResult) -> Self:
        """Returns a copy of this context with `result` recorded, used by
        the runner to build the context for the next stage without mutating
        the one the just-finished stage's child process pickled back."""
        return self.model_copy(update={"completed": {**self.completed, result.stage: result}})
