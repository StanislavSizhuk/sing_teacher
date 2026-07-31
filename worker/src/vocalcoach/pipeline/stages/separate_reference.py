"""Stage P2: isolate the reference song's vocal stem with Demucs (spec
6.4, 6.3.2, ADR-0003). Runs exactly once per song, in the cold path (M2) --
the scheduler never re-enqueues a song whose prep_status is already `ready`,
so this stage no longer needs a cache short-circuit of its own.
"""

from __future__ import annotations

import time
from pathlib import Path

from vocalcoach.audio.io import read_mono, write_mono
from vocalcoach.audio.loudness import measure_and_normalize
from vocalcoach.constants import SEPARATE_REFERENCE_TIMEOUT_SECONDS, TARGET_LOUDNESS_LUFS
from vocalcoach.errors import ReferenceTooQuiet
from vocalcoach.models.context import SongPrepContext
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.base import PipelineStage
from vocalcoach.pipeline.registry import VocalSeparator

STAGE_NAME = "separate_reference"

# Below this integrated loudness (LUFS, ITU-R BS.1770) the isolated stem is
# mostly separation artifacts, not an audible voice (spec 6.8 REFERENCE_TOO_QUIET).
MIN_VOCAL_LOUDNESS_LUFS = -50.0


class SeparateReferenceStage(PipelineStage[SongPrepContext]):
    """`StageResult.data`: `stem_path` (cached for as long as the song row
    exists, spec 7.2), `loudness_lufs`.
    """

    name = STAGE_NAME
    timeout_seconds = SEPARATE_REFERENCE_TIMEOUT_SECONDS

    def __init__(self, separator: VocalSeparator) -> None:
        self._separator = separator

    def run(self, context: SongPrepContext) -> StageResult:
        start = time.monotonic()
        prep_reference = context.result("prep_reference").data
        reference_path = Path(prep_reference["reference_path"])
        sample_rate = int(prep_reference["sample_rate_hz"])

        mixture, _sample_rate = read_mono(reference_path)
        try:
            vocals = self._separator.separate_vocals(mixture, sample_rate)
        finally:
            # Demucs' memory footprint is exactly what spec 6.5 says must
            # never coexist with Whisper's; release it the moment this
            # stage is done with it rather than waiting for process exit.
            self._separator.release()
        normalized, raw_loudness = measure_and_normalize(vocals, sample_rate, TARGET_LOUDNESS_LUFS)

        if raw_loudness < MIN_VOCAL_LOUDNESS_LUFS:
            raise ReferenceTooQuiet(
                f"separated reference vocal stem measured {raw_loudness:.1f} LUFS, "
                f"below the {MIN_VOCAL_LOUDNESS_LUFS} LUFS floor"
            )

        context.vocal_stem_path.parent.mkdir(parents=True, exist_ok=True)
        write_mono(context.vocal_stem_path, normalized, sample_rate)

        return StageResult(
            stage=self.name,
            status=StageStatus.DONE,
            duration_ms=int((time.monotonic() - start) * 1000),
            data={"stem_path": str(context.vocal_stem_path), "loudness_lufs": raw_loudness},
        )
