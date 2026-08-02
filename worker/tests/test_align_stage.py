from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import sine_wave
from tests.helpers import make_context
from vocalcoach.constants import PITCH_HOP_SECONDS
from vocalcoach.errors import AlignmentFailed
from vocalcoach.models.results import StageStatus
from vocalcoach.pipeline.stages.align import AlignStage
from vocalcoach.pipeline.stages.features import FeaturesStage
from vocalcoach.pipeline.stages.preprocess import PreprocessStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _through_separation(tmp_path: Path, recording, reference):
    context = make_context(tmp_path, recording_path=recording, reference_path=reference)
    context = context.with_result(PreprocessStage(ffmpeg_path="ffmpeg").run(context))
    context = context.with_result(FeaturesStage().run(context))
    return context


def test_align_matching_signals_produces_low_distance(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", sine_wave(4.0, 44100, 300.0), 44100)
    reference = wav_writer("reference.wav", sine_wave(4.0, 44100, 300.0), 44100)
    context = _through_separation(tmp_path, recording, reference)

    result = AlignStage().run(context)

    assert result.status == StageStatus.DONE
    assert result.data["length_mismatch"] is False
    assert len(result.data["index1"]) == len(result.data["index2"])
    assert result.data["index1"][0] == 0
    assert result.data["index2"][0] == 0
    # Monotonic non-decreasing warping path (a DTW invariant): compare each
    # index to its successor, so the two zipped sequences are one apart by
    # construction -- strict=True does not apply to this idiom.
    consecutive_pairs = zip(result.data["index1"], result.data["index1"][1:], strict=False)
    assert all(a <= b for a, b in consecutive_pairs)


def test_align_wildly_different_signals_raises_alignment_failed(tmp_path: Path, wav_writer) -> None:
    silence = wav_writer("recording.wav", sine_wave(4.0, 44100, 220.0, amplitude=0.0), 44100)
    noisy = wav_writer(
        "reference.wav",
        (0.5 * np.random.default_rng(0).standard_normal(4 * 44100)).astype("float32"),
        44100,
    )
    context = _through_separation(tmp_path, silence, noisy)

    with pytest.raises(AlignmentFailed):
        AlignStage().run(context)


def test_align_recording_much_shorter_than_reference_crops_and_succeeds(
    tmp_path: Path, wav_writer
) -> None:
    """ADR-0030: a recording cut short (or one that just never reaches the
    reference's own length) used to hard-fail here the moment the length
    difference alone exceeded ALIGN_WINDOW_SECONDS -- dtw-python raised a
    bare ValueError for this exact case pre-migration, and the banded
    kernel's own "unreachable" check does the same today. Matching content
    on the overlap should still align and succeed, flagged as a length
    mismatch rather than failed outright."""
    # Vibrato, not a bare constant tone: a pure unmodulated sine produces
    # identical MFCC frames at every time step, so *any* monotonic path
    # costs the same as any other -- degenerate for DTW, not representative
    # of real singing, and it makes the coarse path's tie-breaking
    # arbitrary enough to occasionally violate the fine pass's much
    # narrower band. Real temporal variation gives both passes an
    # unambiguous, genuinely lowest-cost path to find.
    recording = wav_writer(
        "recording.wav", sine_wave(2.0, 44100, 300.0, vibrato_hz=3.0, vibrato_cents=50.0), 44100
    )
    reference = wav_writer(
        "reference.wav", sine_wave(20.0, 44100, 300.0, vibrato_hz=3.0, vibrato_cents=50.0), 44100
    )
    context = _through_separation(tmp_path, recording, reference)

    result = AlignStage().run(context)

    assert result.status == StageStatus.DONE
    assert result.data["length_mismatch"] is True
    assert len(result.data["index1"]) > 0
    # The 20s reference was cropped to the 2s recording's own length --
    # nowhere near its own full length.
    max_reference_seconds = max(result.data["index2"]) * PITCH_HOP_SECONDS
    assert max_reference_seconds < 4.0


def test_align_recording_much_longer_than_reference_crops_and_succeeds(
    tmp_path: Path, wav_writer
) -> None:
    """Symmetric case: a recording that runs well past the reference's own
    end (e.g. the user kept singing after the backing track stopped)."""
    recording = wav_writer(
        "recording.wav", sine_wave(20.0, 44100, 300.0, vibrato_hz=3.0, vibrato_cents=50.0), 44100
    )
    reference = wav_writer(
        "reference.wav", sine_wave(2.0, 44100, 300.0, vibrato_hz=3.0, vibrato_cents=50.0), 44100
    )
    context = _through_separation(tmp_path, recording, reference)

    result = AlignStage().run(context)

    assert result.status == StageStatus.DONE
    assert result.data["length_mismatch"] is True


def test_align_reference_with_intro_finds_the_recordings_real_start(
    tmp_path: Path, wav_writer
) -> None:
    """ADR-0032: a reference that opens with an instrumental intro (spec
    2.3's `програш` example) sung over by a recording that only starts once
    the user starts singing used to hard-fail here -- frame 0 of the
    recording is actually frame k of the reference, not frame 0, which the
    direct (offset 0) attempt has no way to know. A distinctly different
    intro tone makes the mismatch stark enough that the direct attempt
    reliably fails, so the ADR-0032 fallback search must be what finds it."""
    intro = sine_wave(12.0, 44100, 500.0, vibrato_hz=4.0, vibrato_cents=80.0)
    sung = sine_wave(8.0, 44100, 300.0, vibrato_hz=3.0, vibrato_cents=50.0)
    recording = wav_writer("recording.wav", sung, 44100)
    reference = wav_writer("reference.wav", np.concatenate([intro, sung]), 44100)
    context = _through_separation(tmp_path, recording, reference)

    result = AlignStage().run(context)

    assert result.status == StageStatus.DONE
    assert result.data["reference_start_offset_seconds"] > 0
    # The found offset should land close to the real 12s intro, not some
    # arbitrary other point in the reference.
    assert 10.0 <= result.data["reference_start_offset_seconds"] <= 14.0
    assert len(result.data["index1"]) > 0


def test_align_length_mismatch_with_unrelated_content_still_raises_alignment_failed(
    tmp_path: Path, wav_writer
) -> None:
    """Cropping to the overlap must not rescue a recording whose content
    genuinely doesn't match, even once the lengths are compatible -- the
    fine pass's own normalized-distance ceiling (spec 6.8) still applies
    to whatever was cropped."""
    recording = wav_writer("recording.wav", sine_wave(2.0, 44100, 220.0), 44100)
    reference = wav_writer(
        "reference.wav",
        (0.5 * np.random.default_rng(1).standard_normal(20 * 44100)).astype("float32"),
        44100,
    )
    context = _through_separation(tmp_path, recording, reference)

    with pytest.raises(AlignmentFailed):
        AlignStage().run(context)
