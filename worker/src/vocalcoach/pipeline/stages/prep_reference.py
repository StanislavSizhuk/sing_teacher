"""Stage P1: loudness normalization, resample, mono conversion, canonical
WAV re-encode (spec 6.4, 6.3.1) -- the reference mixture's half of what
used to be the single combined preprocess stage before M2's cold/warm
split. Runs exactly once per song, in the cold path; every later P-stage
(P2 Demucs, P4 reference pitch) reads this stage's output, never the raw
upload again.
"""

from __future__ import annotations

import time

from vocalcoach.audio.ffmpeg import decode_and_normalize
from vocalcoach.constants import (
    PIPELINE_SAMPLE_RATE_HZ,
    PREP_REFERENCE_TIMEOUT_SECONDS,
    TARGET_LOUDNESS_LUFS,
)
from vocalcoach.models.context import SongPrepContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "prep_reference"


class PrepReferenceStage(PipelineStage[SongPrepContext]):
    """`StageResult.data`: `reference_path` (a `work_dir`-scoped canonical
    WAV), `sample_rate_hz`, `reference_loudness_lufs` (pre-normalization,
    for downstream too-quiet checks and observability).
    """

    name = STAGE_NAME
    timeout_seconds = PREP_REFERENCE_TIMEOUT_SECONDS

    def __init__(self, ffmpeg_path: str) -> None:
        self._ffmpeg_path = ffmpeg_path

    def run(self, context: SongPrepContext) -> StageResult:
        start = time.monotonic()
        context.work_dir.mkdir(parents=True, exist_ok=True)

        reference_path = context.work_dir / "reference.wav"
        reference_loudness = decode_and_normalize(
            self._ffmpeg_path,
            context.reference_path,
            reference_path,
            sample_rate_hz=PIPELINE_SAMPLE_RATE_HZ,
            target_loudness_lufs=TARGET_LOUDNESS_LUFS,
            timeout_seconds=self.timeout_seconds,
            stage_name=self.name,
        )

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "reference_path": str(reference_path),
                "sample_rate_hz": PIPELINE_SAMPLE_RATE_HZ,
                "reference_loudness_lufs": reference_loudness,
            },
        )
