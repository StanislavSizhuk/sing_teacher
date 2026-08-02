from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import sine_wave
from tests.helpers import FakeVocalSeparator, make_context, reference_pitch_curve_for
from vocalcoach.constants import PITCH_HOP_SECONDS
from vocalcoach.dsp.features import load_shared_features
from vocalcoach.models.audio import PitchCurve
from vocalcoach.models.context import AnalysisContext
from vocalcoach.models.mode import Mode
from vocalcoach.models.results import StageResult, StageStatus
from vocalcoach.pipeline.registry import PyinPitchDetector, VocalSeparator
from vocalcoach.pipeline.stages.align import AlignStage
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.pitch import PitchStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage
from vocalcoach.pipeline.stages.recording_condition import RecordingConditionStage
from vocalcoach.pipeline.stages.separate_recording import SeparateRecordingStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

_THRESHOLD = 0.15


class _FrontHalfSilencedSeparator:
    """Simulates "successful" separation removing accompaniment from
    exactly the front half of the recording -- a crude stand-in for real
    Demucs (spec 15.2: stage tests never touch the real model), but a
    uniform gain would not do here: a ratio of two medians of the *same*
    signal is scale-invariant, so scaling the whole stem down equally would
    leave `_accompaniment_level` completely unchanged regardless of which
    audio this stage reads. Silencing only part of it is what actually
    distinguishes "read the raw recording" from "read the stem".

    Used to prove `RecordingConditionStage` reads the pre-separation
    recording (ADR-0034), not this stem: reading the stem here would make
    the front half's genuine loudness vanish, silently hiding real
    accompaniment contamination from the user.
    """

    def separate_vocals(self, mixture: np.ndarray, sample_rate_hz: int) -> np.ndarray:
        vocals = mixture.copy()
        vocals[: len(vocals) // 2] = 0.0
        return vocals

    def release(self) -> None:
        pass


def _context_through_features(
    tmp_path: Path,
    recording: Path,
    reference: Path,
    *,
    mode: Mode = "clean",
    reference_pitch: PitchCurve | None = None,
    separator: VocalSeparator | None = None,
) -> AnalysisContext:
    context = make_context(
        tmp_path,
        recording_path=recording,
        reference_path=reference,
        reference_pitch=reference_pitch,
    )
    context = context.model_copy(update={"mode": mode})
    context = context.with_result(PreprocessStage(ffmpeg_path="ffmpeg").run(context))
    if mode == "mixed":
        separate_result = SeparateRecordingStage(separator or FakeVocalSeparator()).run(context)
        context = context.with_result(separate_result)
    return context.with_result(FeaturesStage().run(context))


def _with_pitch_curve(context: AnalysisContext, hz: list[float | None]) -> AnalysisContext:
    return context.with_result(
        StageResult(
            stage="pitch",
            status=StageStatus.DONE,
            duration_ms=1,
            data={"user_pitch_curve": {"hop_seconds": PITCH_HOP_SECONDS, "hz": hz}},
        )
    )


def _frame_count(context: AnalysisContext) -> int:
    features_path = Path(context.result("features").data["features_path"])
    return len(load_shared_features(features_path).user.rms_fine)


def test_flags_loud_unvoiced_recording_as_likely_accompaniment(tmp_path: Path, wav_writer) -> None:
    # A steady, constant-amplitude tone: the fake pitch curve below claims
    # half its frames are unvoiced even though the *audio* itself never gets
    # quieter there -- stands in for an instrument ringing through pauses
    # between phrases at roughly the same loudness as the voice, without
    # needing a real (expensive, model-based) pitch detector.
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 220.0, amplitude=0.8), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 220.0), 44100)
    context = _context_through_features(tmp_path, recording, reference)
    frame_count = _frame_count(context)
    half = frame_count // 2
    context = _with_pitch_curve(context, [None] * half + [220.0] * (frame_count - half))

    result = RecordingConditionStage(_THRESHOLD).run(context)

    assert result.data["accompaniment_detected"] is True
    assert result.data["accompaniment_level"] > _THRESHOLD


def test_does_not_flag_quiet_unvoiced_frames(tmp_path: Path, wav_writer) -> None:
    # Near-silence: unvoiced, but quiet -- a natural pause, not contamination.
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 220.0, amplitude=0.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 220.0), 44100)
    context = _context_through_features(tmp_path, recording, reference)
    context = _with_pitch_curve(context, [None] * _frame_count(context))

    result = RecordingConditionStage(_THRESHOLD).run(context)

    assert result.data["accompaniment_detected"] is False
    assert result.data["accompaniment_level"] == 0.0


def test_does_not_flag_loud_voiced_recording(tmp_path: Path, wav_writer) -> None:
    # Loud, but every frame is "voiced" -- a normal, energetic singing voice,
    # no unvoiced frames at all to carry accompaniment energy.
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 220.0, amplitude=0.8), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 220.0), 44100)
    context = _context_through_features(tmp_path, recording, reference)
    context = _with_pitch_curve(context, [220.0] * _frame_count(context))

    result = RecordingConditionStage(_THRESHOLD).run(context)

    assert result.data["accompaniment_detected"] is False
    assert result.data["accompaniment_level"] == 0.0


def test_real_pitch_stage_on_clean_tone_does_not_flag(tmp_path: Path, wav_writer) -> None:
    """End-to-end with the real (non-faked) pitch detector, not just a
    hand-built curve: a clean sustained tone must not trip the heuristic.

    ADR-0033: extraction now happens in `align`, so exercising the real
    detector means running the real `AlignStage`, not hand-building its
    result -- vibrato, not a bare tone, since align now aligns on pitch
    contour (see test_align_stage.py's own fixtures for why a constant
    tone is degenerate for that).
    """
    signal = sine_wave(3.0, 44100, 300.0, vibrato_hz=5.0, vibrato_cents=40.0)
    recording = wav_writer("recording.wav", signal, 44100)
    reference = wav_writer("reference.wav", signal, 44100)
    reference_pitch = reference_pitch_curve_for(tmp_path, reference)
    context = _context_through_features(
        tmp_path, recording, reference, reference_pitch=reference_pitch
    )
    align_result = AlignStage(PyinPitchDetector()).run(context)
    context = context.with_result(align_result)
    pitch_result = PitchStage().run(context)
    context = context.with_result(pitch_result)

    result = RecordingConditionStage(_THRESHOLD).run(context)

    assert result.data["accompaniment_detected"] is False


# --- FR-29/FR-30 mode reconciliation (spec 6.16, T5/T6) ---------------------


def test_clean_with_accompaniment_warns_but_keeps_declared_mode(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 220.0, amplitude=0.8), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 220.0), 44100)
    context = _context_through_features(tmp_path, recording, reference, mode="clean")
    frame_count = _frame_count(context)
    half = frame_count // 2
    context = _with_pitch_curve(context, [None] * half + [220.0] * (frame_count - half))

    result = RecordingConditionStage(_THRESHOLD).run(context)

    assert result.data["effective_mode"] == "clean"
    assert "ACCOMPANIMENT_IN_CLEAN_MODE" in result.data["warnings"]


def test_mixed_without_accompaniment_downgrades_to_clean(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 220.0, amplitude=0.8), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 220.0), 44100)
    context = _context_through_features(tmp_path, recording, reference, mode="mixed")
    context = _with_pitch_curve(context, [220.0] * _frame_count(context))

    result = RecordingConditionStage(_THRESHOLD).run(context)

    assert result.data["effective_mode"] == "clean"
    assert "MODE_DOWNGRADED_TO_CLEAN" in result.data["warnings"]


def test_mixed_with_accompaniment_is_unremarkable(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 220.0, amplitude=0.8), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 220.0), 44100)
    context = _context_through_features(tmp_path, recording, reference, mode="mixed")
    frame_count = _frame_count(context)
    half = frame_count // 2
    context = _with_pitch_curve(context, [None] * half + [220.0] * (frame_count - half))

    result = RecordingConditionStage(_THRESHOLD).run(context)

    assert result.data["effective_mode"] == "mixed"
    assert result.data["warnings"] == []


def test_mixed_mode_reads_raw_recording_not_the_separated_stem(tmp_path: Path, wav_writer) -> None:
    """ADR-0034 regression: `SeparateRecordingStage` runs before this stage
    in `mixed` (`worker.build_stages`), and the shared feature cache's
    `user` side is now computed from its stem, not the raw recording. The
    fake-unvoiced first half below (`_with_pitch_curve`) is genuinely loud
    in the raw recording (real accompaniment contamination), but
    `_FrontHalfSilencedSeparator` "separates" it down to silence. If
    `RecordingConditionStage` read RMS off the stem/cache instead of the
    raw recording, it would measure that silence and miss the
    contamination entirely -- exactly the bug this stage's own module
    docstring warns about.
    """
    recording = wav_writer("recording.wav", sine_wave(3.0, 44100, 220.0, amplitude=0.8), 44100)
    reference = wav_writer("reference.wav", sine_wave(3.0, 44100, 220.0), 44100)
    context = _context_through_features(
        tmp_path, recording, reference, mode="mixed", separator=_FrontHalfSilencedSeparator()
    )
    frame_count = _frame_count(context)
    half = frame_count // 2
    context = _with_pitch_curve(context, [None] * half + [220.0] * (frame_count - half))

    result = RecordingConditionStage(_THRESHOLD).run(context)

    assert result.data["accompaniment_detected"] is True
    assert result.data["accompaniment_level"] > _THRESHOLD
