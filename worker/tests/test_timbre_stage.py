from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from tests.helpers import build_context_with_identity_align
from vocalcoach.pipeline.stages.timbre import TimbreStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _tone_with_harmonics(
    duration_s: float, sample_rate_hz: int, fundamental_hz: float
) -> np.ndarray:
    t = np.arange(int(duration_s * sample_rate_hz)) / sample_rate_hz
    signal = np.sin(2 * np.pi * fundamental_hz * t)
    signal += 0.5 * np.sin(2 * np.pi * fundamental_hz * 2 * t)
    signal += 0.25 * np.sin(2 * np.pi * fundamental_hz * 3 * t)
    return (0.2 * signal).astype(np.float32)


def test_timbre_identical_spectra_score_near_perfect(tmp_path: Path, wav_writer) -> None:
    recording = wav_writer("recording.wav", _tone_with_harmonics(3.0, 44100, 220.0), 44100)
    reference = wav_writer("reference.wav", _tone_with_harmonics(3.0, 44100, 220.0), 44100)
    # ADR-0033: align now aligns on pitch contour, which a constant tone
    # (tuned for timbre's own spectral comparison, not pitch) is
    # degenerate for -- not what this test is checking (that is
    # test_align_stage.py's job).
    context = build_context_with_identity_align(tmp_path, recording, reference)

    result = TimbreStage().run(context)

    assert result.data["score"] > 90
    assert result.data["mean_cosine_similarity"] > 0.9


def test_timbre_different_spectra_scores_lower_than_identical(tmp_path: Path, wav_writer) -> None:
    # MFCC cosine similarity turns out to be dominated by the overall
    # energy coefficient (c0), so it's fairly forgiving of spectral shape
    # differences once loudness is normalized (spec 6.3.1) -- a relative
    # comparison against the identical-spectra case is a more robust check
    # than an absolute threshold, which is calibration work for later
    # (spec 19: scoring gets tuned against golden fixtures once they exist).
    rng = np.random.default_rng(0)
    noisy = (0.2 * rng.standard_normal(int(3.0 * 44100))).astype(np.float32)
    recording = wav_writer("recording.wav", noisy, 44100)
    reference = wav_writer("reference.wav", _tone_with_harmonics(3.0, 44100, 220.0), 44100)
    # This pair also differs enough in MFCC space to fail real DTW
    # alignment -- align's own concern, not timbre's; use an identity
    # mapping to test this stage's comparison in isolation.
    context = build_context_with_identity_align(tmp_path, recording, reference)

    result = TimbreStage().run(context)

    identical_context = build_context_with_identity_align(tmp_path, reference, reference)
    identical_result = TimbreStage().run(identical_context)

    assert result.data["score"] < identical_result.data["score"]
