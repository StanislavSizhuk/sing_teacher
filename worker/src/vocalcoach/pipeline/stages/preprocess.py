"""Stage A1: loudness normalization, resample, mono conversion, canonical
WAV re-encode (spec 6.3.1, 6.5) -- runs on the user's recording only. The
reference mixture gets the identical treatment once, in the cold path's P1
stage (`prep_reference.py`, spec 6.4, M2); the warm path never re-decodes it.
"""

from __future__ import annotations

import time

from vocalcoach.audio.ffmpeg import decode_and_normalize
from vocalcoach.constants import (
    PIPELINE_SAMPLE_RATE_HZ,
    PREPROCESS_TIMEOUT_SECONDS,
    TARGET_LOUDNESS_LUFS,
)
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "preprocess"


class PreprocessStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `recording_path` (a `work_dir`-scoped canonical
    WAV), `sample_rate_hz`, `recording_loudness_lufs` (pre-normalization,
    for downstream too-quiet checks and observability).
    """

    name = STAGE_NAME
    timeout_seconds = PREPROCESS_TIMEOUT_SECONDS

    def __init__(self, ffmpeg_path: str) -> None:
        self._ffmpeg_path = ffmpeg_path

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        context.work_dir.mkdir(parents=True, exist_ok=True)

        recording_path = context.work_dir / "recording.wav"
        recording_loudness = decode_and_normalize(
            self._ffmpeg_path,
            context.recording_path,
            recording_path,
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
                "recording_path": str(recording_path),
                "sample_rate_hz": PIPELINE_SAMPLE_RATE_HZ,
                "recording_loudness_lufs": recording_loudness,
            },
        )
