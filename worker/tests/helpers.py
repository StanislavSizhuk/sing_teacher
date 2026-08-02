"""Shared test doubles and a helper to build an `AnalysisContext` through
stage A3 (align), so per-aspect stage tests don't each re-derive
preprocessing/alignment from scratch.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from vocalcoach.audio.ffmpeg import decode_and_normalize
from vocalcoach.constants import (
    FEATURES_HOP_SECONDS,
    PIPELINE_SAMPLE_RATE_HZ,
    PITCH_HOP_SECONDS,
    TARGET_LOUDNESS_LUFS,
)
from vocalcoach.models.audio import Lyrics, PitchCurve
from vocalcoach.models.context import AnalysisContext, SongPrepContext
from vocalcoach.models.mode import Mode
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.registry import PyinPitchDetector
from vocalcoach.pipeline.stages.align import AlignStage
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.prep_reference_pitch import PrepReferencePitchStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.separate_recording import SeparateRecordingStage

#: Placeholder for the many stage tests that never read
#: `context.reference_pitch` at all (everything except `PitchStage` itself)
#: -- avoids paying for a real detector run on every `make_context` call
#: just to satisfy the field's type.
EMPTY_REFERENCE_PITCH = PitchCurve(hop_seconds=PITCH_HOP_SECONDS, hz=[])


class FakeVocalSeparator:
    """Stands in for Demucs: this synthetic audio has no instruments to
    remove, so "separation" is the identity function (spec 15.2: stage
    tests never touch the real model)."""

    def separate_vocals(self, mixture: np.ndarray, sample_rate_hz: int) -> np.ndarray:
        return mixture

    def release(self) -> None:
        pass


class FakeTranscriber:
    def __init__(self, lyrics: Lyrics | None = None) -> None:
        self._lyrics = lyrics or Lyrics(language="en", words=[])

    def transcribe(self, samples: np.ndarray, sample_rate_hz: int) -> Lyrics:
        return self._lyrics

    def release(self) -> None:
        pass


def canonical_stem_path(tmp_path: Path, reference_path: Path) -> Path:
    """Decodes/normalizes `reference_path` to `PIPELINE_SAMPLE_RATE_HZ`
    exactly like the cold path's P1 stage would (spec 6.4), so a synthetic
    fixture written at, say, 44.1 kHz lines up frame-for-frame with the
    user recording's own preprocessed (also `PIPELINE_SAMPLE_RATE_HZ`) side
    -- treating a *raw*, differently-rated fixture as if it were already
    the cached stem silently skews every MFCC-hop-based comparison
    downstream (align, timbre) enough to fail alignment outright. Real
    separation (P2) is still skipped here (spec 15.2: stage tests never
    touch Demucs), only the decode/normalize step P1 also does is real.
    """
    stem_path = tmp_path / f"stem-{reference_path.stem}.wav"
    if not stem_path.exists():
        decode_and_normalize(
            "ffmpeg",
            reference_path,
            stem_path,
            sample_rate_hz=PIPELINE_SAMPLE_RATE_HZ,
            target_loudness_lufs=TARGET_LOUDNESS_LUFS,
            timeout_seconds=30,
            stage_name="test-canonicalize-reference",
        )
    return stem_path


def reference_pitch_curve_for(tmp_path: Path, reference_path: Path) -> PitchCurve:
    """Computes a real reference pitch curve for `reference_path`, the same
    way the cold path's P4 stage would (spec 6.4) -- reuses
    `PrepReferencePitchStage` itself rather than re-deriving its detection
    logic here (spec 12.1 DRY). Tests that check `PitchStage`'s actual
    scoring behavior need a curve correlated with the real reference audio,
    not `EMPTY_REFERENCE_PITCH`.
    """
    stem_path = canonical_stem_path(tmp_path, reference_path)
    prep_context = SongPrepContext(
        song_id="test-song",
        reference_path=reference_path,
        work_dir=tmp_path / "prep-work",
        vocal_stem_path=stem_path,
    )
    result = PrepReferencePitchStage(PyinPitchDetector()).run(prep_context)
    return PitchCurve.model_validate(result.data["reference_pitch_curve"])


def make_context(
    tmp_path: Path,
    *,
    recording_path: Path,
    reference_path: Path,
    reference_lyrics: Lyrics | None = None,
    reference_pitch: PitchCurve | None = None,
    mode: Mode = "clean",
) -> AnalysisContext:
    """`reference_path` is canonicalized (`canonical_stem_path`) and then
    treated as if it were already the cold path's cached vocal stem (spec
    6.6, M2) -- stage tests never run real Demucs separation (spec 15.2),
    and the `FakeVocalSeparator` used throughout this module is already the
    identity function, so starting from "already separated" changes nothing
    these tests actually exercise. `reference_pitch` defaults to
    `EMPTY_REFERENCE_PITCH` -- pass a real one (`reference_pitch_curve_for`)
    when the test exercises `PitchStage` itself. `mode="mixed"` alone does
    not run `SeparateRecordingStage` -- callers going through
    `_through_features`/`build_context_through_align` get that for free
    (ADR-0034); a bare `make_context` caller inspecting only `context.mode`
    itself does not need it.
    """
    return AnalysisContext(
        analysis_id="test-analysis",
        user_id="test-user",
        song_id="test-song",
        recording_path=recording_path,
        work_dir=tmp_path / "work",
        reference_vocal_stem_path=canonical_stem_path(tmp_path, reference_path),
        reference_lyrics=reference_lyrics,
        reference_pitch=reference_pitch or EMPTY_REFERENCE_PITCH,
        mode=mode,
    )


def _through_features(
    tmp_path: Path,
    recording_path: Path,
    reference_path: Path,
    *,
    reference_pitch: PitchCurve | None = None,
    mode: Mode = "clean",
) -> AnalysisContext:
    """Runs preprocess -> [separate_recording] -> features for real -- the
    shared setup every stage A4+ test needs, since those stages read
    MFCC/RMS/onsets out of the A3 shared feature cache (spec 6.9) instead of
    computing their own. The reference side needs no stage of its own here
    (spec 6.2, M2): it is already cold-path output by the time the warm path
    ever runs.

    ADR-0034: `mode="mixed"` runs `SeparateRecordingStage` (with the same
    identity-function `FakeVocalSeparator` used throughout this module)
    between preprocess and features, exactly where `worker.build_stages`
    puts it -- so `features`/`align` see the same `voice_audio_path`
    resolution a real mixed-mode analysis would.
    """
    context = make_context(
        tmp_path,
        recording_path=recording_path,
        reference_path=reference_path,
        reference_pitch=reference_pitch,
        mode=mode,
    )

    preprocess_result = PreprocessStage(ffmpeg_path="ffmpeg").run(context)
    context = context.with_result(preprocess_result)

    if mode == "mixed":
        separate_result = SeparateRecordingStage(FakeVocalSeparator()).run(context)
        context = context.with_result(separate_result)

    features_result = FeaturesStage().run(context)
    return context.with_result(features_result)


def build_context_through_align(
    tmp_path: Path,
    recording_path: Path,
    reference_path: Path,
    *,
    reference_pitch: PitchCurve | None = None,
    mode: Mode = "clean",
) -> AnalysisContext:
    """Runs preprocess -> [separate_recording] -> features -> align for
    real, and returns the resulting context so a stage A5+ test can start
    from it.

    ADR-0033: align now aligns on pitch contour, so it needs a real
    reference curve to align against, not `EMPTY_REFERENCE_PITCH` --
    unlike `make_context`'s own default (most tests never read
    `context.reference_pitch` at all), a caller here that doesn't pass one
    gets a real one computed the same way the cold path would
    (`reference_pitch_curve_for`).
    """
    context = _through_features(
        tmp_path,
        recording_path,
        reference_path,
        reference_pitch=reference_pitch or reference_pitch_curve_for(tmp_path, reference_path),
        mode=mode,
    )
    align_result = AlignStage(PyinPitchDetector()).run(context)
    return context.with_result(align_result)


def build_context_with_identity_align(
    tmp_path: Path,
    recording_path: Path,
    reference_path: Path,
    *,
    frame_count: int = 200,
    reference_pitch: PitchCurve | None = None,
) -> AnalysisContext:
    """Like `build_context_through_align`, but injects a trivial identity
    warping path instead of running real DTW.

    Some tests deliberately feed the aspect stages (dynamics, timbre) two
    signals that differ exactly in what that stage measures -- which is
    also what makes them differ enough in MFCC space to legitimately fail
    real alignment first (align mixes timbre and energy information into
    the same distance metric). This decouples such a test from align's own
    threshold tuning, which is unrelated to what the test is checking.
    """
    context = _through_features(
        tmp_path, recording_path, reference_path, reference_pitch=reference_pitch
    )

    identity = list(range(frame_count))
    align_result = StageResult(
        stage="align",
        status=StageStatus.DONE,
        duration_ms=1,
        data={
            "index1": identity,
            "index2": identity,
            "hop_seconds": FEATURES_HOP_SECONDS,
            # ADR-0033: a plausible-shaped placeholder, not a real curve --
            # no test using this identity-mapping helper reads pitch
            # accuracy, but PitchStage's contract now expects this key to
            # exist on any align result.
            "user_pitch_curve": PitchCurve(
                hop_seconds=FEATURES_HOP_SECONDS, hz=[None] * frame_count
            ).model_dump(mode="json"),
        },
    )
    return context.with_result(align_result)
