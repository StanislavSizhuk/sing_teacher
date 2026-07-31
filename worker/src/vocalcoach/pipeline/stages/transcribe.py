"""Stage P3: transcribe the reference vocal stem to words with timecodes via
Whisper (spec 6.4, 6.3.3). Optional (FR-18, spec 6.3): a failure or timeout
here never blocks the song's cold path -- `PipelineRunner` records it as
`StageStatus.SKIPPED` (see `required = False` below) and `SongPrepJobHandler`
writes `lyrics_available = false` instead of failing the whole run.
"""

from __future__ import annotations

import time
from pathlib import Path

from vocalcoach.audio.io import read_mono
from vocalcoach.constants import TRANSCRIBE_TIMEOUT_SECONDS
from vocalcoach.models.context import SongPrepContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import Transcriber

STAGE_NAME = "transcribe"


class TranscribeStage(PipelineStage[SongPrepContext]):
    """`StageResult.data`: `lyrics` (a JSON-encoded `Lyrics`)."""

    name = STAGE_NAME
    timeout_seconds = TRANSCRIBE_TIMEOUT_SECONDS
    required = False

    def __init__(self, transcriber: Transcriber) -> None:
        self._transcriber = transcriber

    def run(self, context: SongPrepContext) -> StageResult:
        start = time.monotonic()

        stem_path = Path(context.result("separate_reference").data["stem_path"])
        samples, sample_rate = read_mono(stem_path)
        try:
            lyrics = self._transcriber.transcribe(samples, sample_rate)
        finally:
            # Whisper's memory footprint is exactly what spec 6.5 says must
            # never coexist with Demucs'; release it as soon as this stage
            # is done rather than waiting for process exit.
            self._transcriber.release()

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"lyrics": lyrics.model_dump(mode="json")},
        )
