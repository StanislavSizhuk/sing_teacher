"""Stage 1: loudness normalization, resample, mono conversion, canonical WAV
re-encode (spec 6.3.1) -- runs on both the user's recording and the
reference mixture before anything else touches them.
"""

from __future__ import annotations

import time
from pathlib import Path

from vocalcoach.audio.ffmpeg import canonicalize_for_pipeline
from vocalcoach.audio.io import read_mono, write_mono
from vocalcoach.audio.loudness import measure_and_normalize
from vocalcoach.constants import (
    PIPELINE_SAMPLE_RATE_HZ,
    PREPROCESS_TIMEOUT_SECONDS,
    TARGET_LOUDNESS_LUFS,
)
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage

STAGE_NAME = "preprocess"


class PreprocessStage(PipelineStage):
    """`StageResult.data`: `recording_path`, `reference_path` (both
    `work_dir`-scoped canonical WAVs), `sample_rate_hz`,
    `recording_loudness_lufs`, `reference_loudness_lufs` (pre-normalization,
    for downstream too-quiet checks and observability).
    """

    name = STAGE_NAME
    timeout_seconds = PREPROCESS_TIMEOUT_SECONDS

    def __init__(self, ffmpeg_path: str) -> None:
        self._ffmpeg_path = ffmpeg_path

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        context.work_dir.mkdir(parents=True, exist_ok=True)

        recording_path, recording_loudness = self._canonicalize(
            context.recording_path, context.work_dir / "recording.wav"
        )
        reference_path, reference_loudness = self._canonicalize(
            context.reference_path, context.work_dir / "reference.wav"
        )

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={
                "recording_path": str(recording_path),
                "reference_path": str(reference_path),
                "sample_rate_hz": PIPELINE_SAMPLE_RATE_HZ,
                "recording_loudness_lufs": recording_loudness,
                "reference_loudness_lufs": reference_loudness,
            },
        )

    def _canonicalize(self, src: Path, dst: Path) -> tuple[Path, float]:
        resampled = dst.with_suffix(".resampled.wav")
        canonicalize_for_pipeline(
            self._ffmpeg_path,
            src,
            resampled,
            sample_rate_hz=PIPELINE_SAMPLE_RATE_HZ,
            timeout_seconds=self.timeout_seconds,
            stage_name=self.name,
        )
        samples, sample_rate = read_mono(resampled)
        normalized, raw_loudness = measure_and_normalize(samples, sample_rate, TARGET_LOUDNESS_LUFS)
        write_mono(dst, normalized, sample_rate)
        resampled.unlink(missing_ok=True)
        return dst, raw_loudness
