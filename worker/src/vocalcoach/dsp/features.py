"""Shared per-audio feature cache (spec 6.9): every representation a later
stage needs, computed exactly once per file. Before this existed, `align`
and `timbre` each ran their own identical MFCC extraction, and `dynamics`
and `breath` each ran their own identical RMS envelope extraction -- four
calls into `librosa` where two sufficed. `FeaturesStage` calls
`compute_shared_features` once per side (user recording, reference stem)
and every stage past it reads the result back off disk instead of touching
`librosa` directly (spec 6.20's anti-pattern list bans exactly the
recomputation this replaces).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

from vocalcoach.audio.io import read_mono
from vocalcoach.constants import FEATURES_HOP_SECONDS, FEATURES_MFCC_COEFFICIENTS, PITCH_HOP_SECONDS


@dataclass(frozen=True)
class SharedFeatures:
    """One audio file's cached representations, each computed once.

    Args:
        mfcc: `(n_frames, FEATURES_MFCC_COEFFICIENTS)` at `FEATURES_HOP_SECONDS`
            -- feeds both alignment (spec 6.7) and timbre comparison (6.3.9).
        rms_envelope: Frame-wise RMS amplitude at `FEATURES_HOP_SECONDS` --
            feeds both dynamics (6.3.8) and breath/pause detection (6.3.10).
        rms_fine: Frame-wise RMS amplitude at `PITCH_HOP_SECONDS`, matching
            the pitch curve's own frame rate -- feeds the recording-condition
            heuristic (6.9/2.3), which compares energy frame-for-frame
            against pitch voicing.
        onset_times: Onset timestamps in seconds (6.3.6).
    """

    mfcc: np.ndarray
    rms_envelope: np.ndarray
    rms_fine: np.ndarray
    onset_times: np.ndarray


def compute_shared_features(path: Path) -> SharedFeatures:
    """Reads `path` once and derives every representation later stages need
    from that one in-memory signal (spec 6.9: "кожне представлення
    рахується не більше одного разу на аудіо за весь пайплайн").
    """
    samples, sample_rate = read_mono(path)

    hop_length = max(1, round(sample_rate * FEATURES_HOP_SECONDS))
    mfcc = librosa.feature.mfcc(
        y=samples, sr=sample_rate, n_mfcc=FEATURES_MFCC_COEFFICIENTS, hop_length=hop_length
    )
    rms_envelope = librosa.feature.rms(y=samples, hop_length=hop_length)[0]

    fine_hop_length = max(1, round(sample_rate * PITCH_HOP_SECONDS))
    rms_fine = librosa.feature.rms(y=samples, hop_length=fine_hop_length)[0]

    onset_times = librosa.onset.onset_detect(y=samples, sr=sample_rate, units="time")

    return SharedFeatures(
        mfcc=np.asarray(mfcc.T, dtype=np.float32),  # (n_frames, n_mfcc), one row per time step
        rms_envelope=np.asarray(rms_envelope, dtype=np.float32),
        rms_fine=np.asarray(rms_fine, dtype=np.float32),
        onset_times=np.asarray(onset_times, dtype=np.float64),
    )


def save_shared_features(path: Path, *, user: SharedFeatures, reference: SharedFeatures) -> None:
    """Persists both sides' features to one `.npz` file in the analysis
    work_dir -- a file path, not raw arrays, crosses `StageResult` into
    `stages_json` (spec 7.3: dense arrays never go into JSONB), exactly like
    stage 1's canonical WAVs already do.
    """
    np.savez(
        path,
        user_mfcc=user.mfcc,
        user_rms_envelope=user.rms_envelope,
        user_rms_fine=user.rms_fine,
        user_onset_times=user.onset_times,
        reference_mfcc=reference.mfcc,
        reference_rms_envelope=reference.rms_envelope,
        reference_rms_fine=reference.rms_fine,
        reference_onset_times=reference.onset_times,
    )


@dataclass(frozen=True)
class LoadedFeatures:
    """Both sides' `SharedFeatures`, reloaded from the `.npz` a `FeaturesStage`
    run earlier this pipeline wrote."""

    user: SharedFeatures
    reference: SharedFeatures


def load_shared_features(path: Path) -> LoadedFeatures:
    with np.load(path) as data:
        return LoadedFeatures(
            user=SharedFeatures(
                mfcc=data["user_mfcc"],
                rms_envelope=data["user_rms_envelope"],
                rms_fine=data["user_rms_fine"],
                onset_times=data["user_onset_times"],
            ),
            reference=SharedFeatures(
                mfcc=data["reference_mfcc"],
                rms_envelope=data["reference_rms_envelope"],
                rms_fine=data["reference_rms_fine"],
                onset_times=data["reference_onset_times"],
            ),
        )
