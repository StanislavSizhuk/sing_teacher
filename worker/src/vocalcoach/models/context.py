"""The `AnalysisContext` every stage receives (spec 6.1): paths to audio,
prior stage results, and this run's config snapshot.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from vocalcoach.config import PitchEngine
from vocalcoach.models.audio import Lyrics, PitchCurve
from vocalcoach.models.results import StageResult


class AnalysisContext(BaseModel):
    """Passed by value into each stage's own child process (spec 6.5), so it
    and everything it holds must be picklable -- pydantic models are.
    Mutations a stage makes to `completed` in its own process never leak
    back; `PipelineRunner` merges the returned `StageResult` into the
    context it keeps for the next stage.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    analysis_id: str
    user_id: str
    song_id: str

    recording_path: Path
    reference_path: Path
    work_dir: Path

    song_content_hash: str
    vocal_stem_processed: bool
    reference_vocal_stem_path: Path | None = None
    reference_lyrics: Lyrics | None = None
    reference_pitch: PitchCurve | None = None

    pitch_engine: PitchEngine
    whisper_model: str
    demucs_model: str
    model_weights_dir: Path

    completed: dict[str, StageResult] = Field(default_factory=dict)

    def result(self, stage_name: str) -> StageResult:
        found = self.completed.get(stage_name)
        if found is None:
            raise KeyError(f"stage '{stage_name}' has not completed yet")
        return found

    def with_result(self, result: StageResult) -> AnalysisContext:
        """Returns a copy of this context with `result` recorded, used by
        the runner to build the context for the next stage without mutating
        the one the just-finished stage's child process pickled back."""
        return self.model_copy(update={"completed": {**self.completed, result.stage: result}})
