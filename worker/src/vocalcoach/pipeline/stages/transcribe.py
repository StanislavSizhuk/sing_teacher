"""Stage 3: transcribe the reference vocal stem to words with timecodes via
Whisper (spec 6.3.3). Short-circuits to the cached transcript when
`context.vocal_stem_processed` is already true (spec 6.6).
"""

from __future__ import annotations

import time
from pathlib import Path

from vocalcoach.audio.io import read_mono
from vocalcoach.constants import TRANSCRIBE_TIMEOUT_SECONDS
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import Transcriber
from vocalcoach.repositories.interfaces import SongRepository

STAGE_NAME = "transcribe"


class TranscribeStage(PipelineStage):
    """`StageResult.data`: `lyrics` (a JSON-encoded `Lyrics`), `cached`
    (whether Whisper actually ran).
    """

    name = STAGE_NAME
    timeout_seconds = TRANSCRIBE_TIMEOUT_SECONDS

    def __init__(self, transcriber: Transcriber, songs: SongRepository) -> None:
        self._transcriber = transcriber
        self._songs = songs

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()

        if context.vocal_stem_processed and context.reference_lyrics is not None:
            return StageResult(
                stage=self.name,
                status=StageStatus.DONE,
                duration_ms=int((time.monotonic() - start) * 1000),
                data={"lyrics": context.reference_lyrics.model_dump(mode="json"), "cached": True},
            )

        stem_path = Path(context.result("separate_reference").data["stem_path"])
        samples, sample_rate = read_mono(stem_path)
        try:
            lyrics = self._transcriber.transcribe(samples, sample_rate)
        finally:
            # Whisper's memory footprint is exactly what spec 6.5 says must
            # never coexist with Demucs'; release it as soon as this stage
            # is done rather than waiting for process exit.
            self._transcriber.release()
        self._songs.save_lyrics(context.song_id, lyrics)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"lyrics": lyrics.model_dump(mode="json"), "cached": False},
        )
