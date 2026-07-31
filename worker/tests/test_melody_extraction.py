"""T4 (spec 15.2) / spec 6.6's M3 go/no-go spike: mixed-mode melody
extraction accuracy on synthetic fixtures at SNR 0 dB and -6 dB.

Spec 6.6: "Критерій прийняття: медіанна помилка F0 < 50 центів на вокальних
кадрах" -- this is that measurement, made permanent as a regression test
(spec 15.1's ML regression tier) rather than a one-off script, so a later
change to `dsp/melody.py` cannot silently regress past the documented
go decision (`docs/adr/0025-melody-extraction-dsp-not-onnx.md`).

The fixture is deliberately not the easiest case the algorithm handles: the
melody's held notes are diatonic to the accompaniment's chords (a singer
performing in key, not some unrelated interval), which is the realistic and
harder case for a harmonic-salience method -- see `dsp/melody.py`'s module
docstring for why a *held* accompaniment note is the actual difficulty, not
mere loudness.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.conftest import harmonic_tone
from vocalcoach.dsp.melody import extract_melody

SAMPLE_RATE_HZ = 22050
HOP_SECONDS = 0.01

# G3-A3-B3-A3: a short diatonic phrase, portamento between notes, vibrato on
# each held note -- what `harmonic_tone` needs is one Hz value per sample.
_MELODY_NOTES_HZ = (196.00, 220.00, 246.94, 220.00)
_NOTE_DURATION_S = 0.9
_PORTAMENTO_S = 0.08
_VIBRATO_HZ = 5.5
_VIBRATO_CENTS = 35.0

# D-G power chords under the melody: both chords share tones with the
# melody's own notes (196/220/246.94 Hz), the harmonically-confusable case
# `dsp/melody.py`'s background subtraction exists for.
_CHORDS_HZ = ((146.83, 174.61, 220.0), (98.0, 146.83, 220.0))
_CHORD_DURATION_S = 1.8
_ACCOMPANIMENT_HARMONICS = 3
_ACCOMPANIMENT_NOISE_AMPLITUDE = 0.05

# spec 6.6's own acceptance threshold.
_MAX_MEDIAN_CENTS_ERROR = 50.0
# A missed (None) vocal frame is scored as a large error rather than
# excluded -- silently dropping misses would let a low-recall extractor pass
# by only ever grading the frames it was confident about.
_MISSED_FRAME_PENALTY_CENTS = 4800.0


def _melodic_phrase_f0_curve(sample_rate_hz: int) -> np.ndarray:
    """One Hz value per *sample* (not per hop) so `harmonic_tone` can
    synthesize continuous phase -- portamento between notes plus vibrato on
    each held note, the way a sung phrase actually moves."""
    per_note = int(_NOTE_DURATION_S * sample_rate_hz)
    n = per_note * len(_MELODY_NOTES_HZ)
    t = np.arange(n) / sample_rate_hz
    base = np.repeat(_MELODY_NOTES_HZ, per_note).astype(np.float64)

    glide_samples = int(_PORTAMENTO_S * sample_rate_hz)
    for boundary in range(per_note, n, per_note):
        lo, hi = boundary - glide_samples // 2, boundary + glide_samples // 2
        if lo < 0 or hi > n:
            continue
        base[lo:hi] = np.linspace(base[lo], base[hi - 1], hi - lo)

    vibrato = 2.0 ** ((_VIBRATO_CENTS / 1200.0) * np.sin(2 * np.pi * _VIBRATO_HZ * t))
    return base * vibrato


def _chord_accompaniment(n_samples: int, sample_rate_hz: int) -> np.ndarray:
    """A held chord progression: each note a small harmonic series of its
    own, plus a little noise -- an instrument bed, not a pure tone."""
    t = np.arange(n_samples) / sample_rate_hz
    signal = np.zeros(n_samples, dtype=np.float64)
    rng = np.random.default_rng(1)  # fixed seed: deterministic fixture (spec 12.3)
    per_chord = int(_CHORD_DURATION_S * sample_rate_hz)
    for chord_index, chord in enumerate(_CHORDS_HZ):
        start = chord_index * per_chord
        end = min(n_samples, start + per_chord)
        for note_hz in chord:
            for harmonic in range(1, _ACCOMPANIMENT_HARMONICS + 1):
                signal[start:end] += (0.5 ** (harmonic - 1)) * np.sin(
                    2 * np.pi * note_hz * harmonic * t[start:end]
                )
    signal += _ACCOMPANIMENT_NOISE_AMPLITUDE * rng.standard_normal(n_samples)
    return signal


def _mix_at_snr(vocal: np.ndarray, accompaniment: np.ndarray, snr_db: float) -> np.ndarray:
    vocal_rms = float(np.sqrt(np.mean(vocal.astype(np.float64) ** 2)))
    accompaniment_rms = float(np.sqrt(np.mean(accompaniment**2)))
    target_accompaniment_rms = vocal_rms / (10.0 ** (snr_db / 20.0))
    scaled_accompaniment = accompaniment * (target_accompaniment_rms / (accompaniment_rms + 1e-12))
    return (vocal.astype(np.float64) + scaled_accompaniment).astype(np.float32)


def _median_cents_error(estimated_hz: list[float | None], truth_hz: np.ndarray) -> float:
    """`truth_hz` is per-sample; `estimated_hz` is per `HOP_SECONDS` frame."""
    hop_length = round(HOP_SECONDS * SAMPLE_RATE_HZ)
    errors: list[float] = []
    for frame_index, hz in enumerate(estimated_hz):
        sample_index = frame_index * hop_length
        if sample_index >= len(truth_hz):
            break
        if hz is None:
            errors.append(_MISSED_FRAME_PENALTY_CENTS)
            continue
        errors.append(abs(1200.0 * np.log2(hz / truth_hz[sample_index])))
    return float(np.median(errors))


@pytest.mark.parametrize("snr_db", [0.0, -6.0])
def test_melody_extraction_meets_spike_criterion(snr_db: float) -> None:
    truth_hz = _melodic_phrase_f0_curve(SAMPLE_RATE_HZ)
    vocal = harmonic_tone(truth_hz, SAMPLE_RATE_HZ)
    accompaniment = _chord_accompaniment(len(vocal), SAMPLE_RATE_HZ)
    mixture = _mix_at_snr(vocal, accompaniment, snr_db)

    estimated_hz = extract_melody(mixture, SAMPLE_RATE_HZ, HOP_SECONDS)

    median_error = _median_cents_error(estimated_hz, truth_hz)
    assert median_error < _MAX_MEDIAN_CENTS_ERROR, (
        f"median F0 error {median_error:.1f} cents at SNR {snr_db} dB exceeds "
        f"spec 6.6's {_MAX_MEDIAN_CENTS_ERROR} cent go/no-go threshold"
    )
