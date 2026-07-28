"""Stage 2: isolate the reference song's vocal stem with Demucs (spec
6.3.2, ADR-0003). Short-circuits to the cached stem file when
`context.vocal_stem_processed` is already true (spec 6.6).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from vocalcoach.audio.io import read_mono, write_mono
from vocalcoach.audio.loudness import measure_and_normalize
from vocalcoach.constants import SEPARATE_REFERENCE_TIMEOUT_SECONDS, TARGET_LOUDNESS_LUFS
from vocalcoach.errors import ReferenceTooQuiet
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import VocalSeparator

STAGE_NAME = "separate_reference"

# Below this integrated loudness (LUFS, ITU-R BS.1770) the isolated stem is
# mostly separation artifacts, not an audible voice (spec 6.8 REFERENCE_TOO_QUIET).
MIN_VOCAL_LOUDNESS_LUFS = -50.0


class SeparateReferenceStage(PipelineStage):
    """`StageResult.data`: `stem_path` (cached across analyses of this
    song), `loudness_lufs`, `cached` (whether Demucs actually ran).
    """

    name = STAGE_NAME
    timeout_seconds = SEPARATE_REFERENCE_TIMEOUT_SECONDS

    def __init__(
        self, separator: VocalSeparator, stem_path_for_song: Callable[[str], Path]
    ) -> None:
        self._separator = separator
        self._stem_path_for_song = stem_path_for_song

    def run(self, context: AnalysisContext) -> StageResult:
        start = time.monotonic()
        stem_path = self._stem_path_for_song(context.song_id)

        if context.vocal_stem_processed:
            return StageResult(
                stage=self.name,
                status=StageStatus.DONE,
                duration_ms=int((time.monotonic() - start) * 1000),
                data={"stem_path": str(stem_path), "cached": True},
            )

        preprocess = context.result("preprocess").data
        reference_path = Path(preprocess["reference_path"])
        sample_rate = int(preprocess["sample_rate_hz"])

        mixture, _sample_rate = read_mono(reference_path)
        vocals = self._separator.separate_vocals(mixture, sample_rate)
        normalized, raw_loudness = measure_and_normalize(vocals, sample_rate, TARGET_LOUDNESS_LUFS)

        if raw_loudness < MIN_VOCAL_LOUDNESS_LUFS:
            raise ReferenceTooQuiet(
                f"separated reference vocal stem measured {raw_loudness:.1f} LUFS, "
                f"below the {MIN_VOCAL_LOUDNESS_LUFS} LUFS floor"
            )

        stem_path.parent.mkdir(parents=True, exist_ok=True)
        write_mono(stem_path, normalized, sample_rate)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"stem_path": str(stem_path), "loudness_lufs": raw_loudness, "cached": False},
        )
