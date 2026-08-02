"""Stage A1b: isolate the user's own vocal from their `mixed`-mode recording
with Demucs (ADR-0034), the same `VocalSeparator` `SeparateReferenceStage`
already uses on the reference. `clean` never runs this stage at all
(`modes = {"mixed"}`) -- there is no accompaniment to remove.

Runs once per analysis, right after `preprocess`, in the warm path -- unlike
the reference's stem, this one cannot be cached: every recording is unique.
`pipeline/voice_source.py::voice_audio_path` is the one place that reads
this stage's output back; every stage past it (`features`, `align`) sees an
isolated vocal, not the raw mixture.
"""

from __future__ import annotations

import time
from pathlib import Path

from vocalcoach.audio.io import read_mono, write_mono
from vocalcoach.audio.loudness import measure_and_normalize
from vocalcoach.constants import (
    MIN_VOCAL_LOUDNESS_LUFS,
    SEPARATE_RECORDING_TIMEOUT_SECONDS,
    TARGET_LOUDNESS_LUFS,
)
from vocalcoach.errors import NoVoiceDetected
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import VocalSeparator

STAGE_NAME = "separate_recording"


class SeparateRecordingStage(PipelineStage[AnalysisContext]):
    """`StageResult.data`: `stem_path` (ephemeral, `work_dir`-scoped -- torn
    down with the rest of `work_dir` on completion, spec 5 audio retention,
    unlike the reference's stem which is cached for the song's lifetime),
    `loudness_lufs`.
    """

    name = STAGE_NAME
    modes = frozenset({"mixed"})
    timeout_seconds = SEPARATE_RECORDING_TIMEOUT_SECONDS

    def __init__(self, separator: VocalSeparator) -> None:
        self._separator = separator

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        preprocess = context.result("preprocess").data
        recording_path = Path(preprocess["recording_path"])
        sample_rate = int(preprocess["sample_rate_hz"])

        mixture, _sample_rate = read_mono(recording_path)
        try:
            vocals = self._separator.separate_vocals(mixture, sample_rate)
        finally:
            # Same spec 6.5 hygiene as SeparateReferenceStage: release before
            # this stage's own subprocess exits, not after.
            self._separator.release()
        normalized, raw_loudness = measure_and_normalize(vocals, sample_rate, TARGET_LOUDNESS_LUFS)

        if raw_loudness < MIN_VOCAL_LOUDNESS_LUFS:
            raise NoVoiceDetected(
                f"separated recording vocal stem measured {raw_loudness:.1f} LUFS, "
                f"below the {MIN_VOCAL_LOUDNESS_LUFS} LUFS floor"
            )

        stem_path = context.work_dir / "recording_vocals.wav"
        write_mono(stem_path, normalized, sample_rate)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"stem_path": str(stem_path), "loudness_lufs": raw_loudness},
        )
