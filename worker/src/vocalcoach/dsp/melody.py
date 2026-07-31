"""Melody extraction (spec 6.5/6.6, stage A4, `mixed` mode only): recovers
the vocal F0 curve directly from a polyphonic mixture, without running
source separation on the user's recording (spec 2.3 -- Demucs never runs on
the user's own take, only on the reference, ADR-0003).

Spec 6.6 names an ONNX melody-extraction model as the target implementation.
No such model ships pinned/checksummed weights this project can vendor
(spec 11.3 requires a fixed checksum verified at worker startup -- there is
no equivalent here to Silero VAD's small, well-known model), so this spike
evaluates a classical harmonic-summation salience method instead: the same
family of technique Melodia-style melody extractors use before any learned
model became standard, built entirely from `numpy`/`scipy`, already
dependencies. See `docs/adr/0025-melody-extraction-dsp-not-onnx.md` for the
go/no-go decision this module's accuracy measurement (`tests/
test_melody_extraction.py`, spec 15.2 T4) feeds.

Algorithm, per analysis frame:

1. STFT magnitude spectrum of the mixture.
2. For a fine grid of candidate F0s spanning the vocal range, sum the
   magnitude at each candidate's first `MELODY_HARMONICS` harmonics,
   weighted by a decaying series (spec: a real voice's harmonics decay in
   amplitude; weighting later, weaker harmonics less keeps a strong
   accompaniment harmonic from dominating a single term). This is "salience":
   the candidate whose implied harmonic series best explains the observed
   spectrum wins, which is far more selective than reading off the single
   loudest bin -- an accompaniment note's own fundamental rarely also
   explains the vocal's higher harmonics.
3. Rolling-window background subtraction: for each candidate, subtract the
   median salience that same candidate held over the trailing
   `MELODY_BACKGROUND_WINDOW_SECONDS` before picking the frame's winner. An
   accompaniment note sits at a fixed pitch for as long as it rings, so its
   candidate column stays loud across many consecutive frames; a sung
   melody moves continuously (portamento, vibrato), so no single candidate
   column it passes through stays the loudest for long. Subtracting each
   column's own recent median leaves a moving voice largely intact while
   suppressing a held accompaniment note -- without this step, raw salience
   plainly prefers whichever source is louder, vocal or not (measured: a
   held chord under the vocal wins outright below 0 dB SNR without this
   step; median cents error stays under the spec 6.6 threshold at both 0 dB
   and -6 dB with it, see `tests/test_melody_extraction.py`).
4. The candidate with peak *suppressed* salience is the frame's F0 estimate;
   voicing is decided by how much of the frame's total spectral energy that
   peak accounts for -- a real harmonic series concentrates energy at its
   own harmonics, noise/silence does not.

Known limitation, worth stating rather than hiding (spec G7): a note held
perfectly steady (no vibrato, no portamento) for longer than the background
window gets partly suppressed by its own recent history, the same as a
static accompaniment note would. `aspect_confidence` for pitch/vibrato in
`mixed` mode is lowered accordingly when this stage's own voicing ratio runs
low (spec 6.15) rather than silently reporting an unqualified number.

Every step above is `numpy` array operations across (frame, candidate,
harmonic) at once -- no dense per-frame Python loop (NFR-17). Frames are
still processed in bounded chunks (`_CHUNK_FRAMES`) so a multi-minute
recording's candidate x harmonic x frame tensor cannot exceed a fixed memory
ceiling, the same bounded-resource principle as the banded DTW's corridor
(spec NFR-16).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter

from vocalcoach.constants import (
    MELODY_BACKGROUND_WINDOW_SECONDS,
    MELODY_CANDIDATE_CENTS_STEP,
    MELODY_CHUNK_FRAMES,
    MELODY_HARMONIC_WEIGHT_DECAY,
    MELODY_HARMONICS,
    MELODY_HOP_SECONDS,
    MELODY_MEDIAN_FILTER_FRAMES,
    MELODY_N_FFT,
    MELODY_OCTAVE_JUMP_TOLERANCE_CENTS,
    MELODY_VOICING_SALIENCE_RATIO,
    MELODY_WINDOW_SECONDS,
    PITCH_FMAX_HZ,
    PITCH_FMIN_HZ,
)

_EPS = 1e-12


@dataclass(frozen=True)
class _CandidateGrid:
    """Precomputed, frequency-independent part of the salience computation:
    which (candidate, harmonic) pair maps to which pair of adjacent FFT bins
    to linearly interpolate between, and the harmonic weights. Built once
    per call, reused for every frame chunk.
    """

    hz: np.ndarray  # (n_candidates,)
    bin_lo: np.ndarray  # (n_candidates, n_harmonics), int
    bin_hi: np.ndarray  # (n_candidates, n_harmonics), int
    frac: np.ndarray  # (n_candidates, n_harmonics), in [0, 1]
    harmonic_weights: np.ndarray  # (n_harmonics,)


def _build_candidate_grid(sample_rate_hz: int, n_fft: int) -> _CandidateGrid:
    cents_span = 1200.0 * np.log2(PITCH_FMAX_HZ / PITCH_FMIN_HZ)
    n_candidates = max(2, round(cents_span / MELODY_CANDIDATE_CENTS_STEP) + 1)
    cents = np.linspace(0.0, cents_span, n_candidates)
    candidate_hz = PITCH_FMIN_HZ * (2.0 ** (cents / 1200.0))

    harmonics = np.arange(1, MELODY_HARMONICS + 1, dtype=np.float64)
    target_hz = candidate_hz[:, None] * harmonics[None, :]  # (n_candidates, n_harmonics)

    freq_resolution = sample_rate_hz / n_fft
    n_bins = n_fft // 2 + 1
    bin_pos = target_hz / freq_resolution
    bin_lo = np.clip(np.floor(bin_pos).astype(np.int64), 0, n_bins - 1)
    bin_hi = np.clip(bin_lo + 1, 0, n_bins - 1)
    frac = np.clip(bin_pos - bin_lo, 0.0, 1.0)

    harmonic_weights = MELODY_HARMONIC_WEIGHT_DECAY ** (harmonics - 1.0)

    return _CandidateGrid(
        hz=candidate_hz, bin_lo=bin_lo, bin_hi=bin_hi, frac=frac, harmonic_weights=harmonic_weights
    )


def _salience_best_f0(
    magnitude: np.ndarray, grid: _CandidateGrid, background_window_frames: int
) -> tuple[np.ndarray, np.ndarray]:
    """`magnitude`: `(n_frames, n_bins)`. Returns `(best_hz, voicing_ratio)`,
    both `(n_frames,)` -- the winning candidate per frame, after background
    subtraction (module docstring), and the fraction of that frame's total
    spectral energy the winning candidate's suppressed salience accounts for.
    """
    lo = magnitude[:, grid.bin_lo]  # (n_frames, n_candidates, n_harmonics)
    hi = magnitude[:, grid.bin_hi]
    interpolated = lo * (1.0 - grid.frac)[None, :, :] + hi * grid.frac[None, :, :]
    salience = np.tensordot(interpolated, grid.harmonic_weights, axes=([2], [0]))  # (n_frames, n_c)

    # Per-candidate rolling median over time, native scipy code (NFR-17) --
    # a static accompaniment note's column stays loud across this whole
    # window; a moving melody's column does not (module docstring).
    window = max(1, background_window_frames | 1)  # scipy needs an odd size
    background = median_filter(salience, size=(window, 1), mode="nearest")
    suppressed = np.clip(salience - background, 0.0, None)

    best_index = np.argmax(suppressed, axis=1)
    frame_index = np.arange(magnitude.shape[0])
    best_salience = suppressed[frame_index, best_index]
    best_hz = grid.hz[best_index]

    total_energy = magnitude.sum(axis=1) + _EPS
    voicing_ratio = best_salience / total_energy
    return best_hz, voicing_ratio


def _postprocess(hz: list[float | None]) -> list[float | None]:
    """Median filter plus octave-jump rejection (spec 6.5's mandatory pitch
    post-processing): a frame-to-frame jump of within tolerance of exactly
    one octave is treated as the salience picker locking onto the wrong
    harmonic, not a real pitch change, and is folded back to the nearer
    octave of its neighbor. Runs after the median filter so the filter first
    removes single-frame spikes that would otherwise skew the neighbor
    comparison.
    """
    filtered = _median_filter(hz, MELODY_MEDIAN_FILTER_FRAMES)
    return _fix_octave_jumps(filtered)


def _median_filter(hz: list[float | None], window: int) -> list[float | None]:
    if window < 3 or window % 2 == 0:
        return list(hz)
    half = window // 2
    result: list[float | None] = []
    for i in range(len(hz)):
        if hz[i] is None:
            result.append(None)
            continue
        neighborhood = [v for v in hz[max(0, i - half) : i + half + 1] if v is not None]
        result.append(float(np.median(neighborhood)) if neighborhood else None)
    return result


def _fix_octave_jumps(hz: list[float | None]) -> list[float | None]:
    result = list(hz)
    previous: float | None = None
    for i, value in enumerate(result):
        if value is None:
            continue
        if previous is not None:
            cents_from_previous = 1200.0 * np.log2(value / previous)
            for octave_shift in (-2, -1, 1, 2):
                target_cents = 1200.0 * octave_shift
                if abs(cents_from_previous - target_cents) <= MELODY_OCTAVE_JUMP_TOLERANCE_CENTS:
                    value = value / (2.0**octave_shift)
                    result[i] = value
                    break
        previous = value
    return result


def extract_melody(
    mixture: np.ndarray, sample_rate_hz: int, hop_seconds: float = MELODY_HOP_SECONDS
) -> list[float | None]:
    """Extracts the vocal F0 curve from a polyphonic `mixture` (spec 6.6,
    A4). Returns one Hz value per `hop_seconds` frame, `None` where the
    salience picker found no convincing harmonic series (spec: unvoiced/no
    dominant vocal in that frame).
    """
    import librosa

    win_length = max(1, round(sample_rate_hz * MELODY_WINDOW_SECONDS))
    hop_length = max(1, round(sample_rate_hz * hop_seconds))
    n_fft = max(MELODY_N_FFT, win_length)

    stft = librosa.stft(mixture, n_fft=n_fft, hop_length=hop_length, win_length=win_length)
    magnitude = np.abs(stft).T.astype(np.float64)  # (n_frames, n_bins)

    grid = _build_candidate_grid(sample_rate_hz, n_fft)
    background_window_frames = max(1, round(MELODY_BACKGROUND_WINDOW_SECONDS / hop_seconds))

    raw_hz: list[float | None] = []
    for start in range(0, magnitude.shape[0], MELODY_CHUNK_FRAMES):
        chunk = magnitude[start : start + MELODY_CHUNK_FRAMES]
        best_hz, voicing_ratio = _salience_best_f0(chunk, grid, background_window_frames)
        raw_hz.extend(
            float(hz) if ratio >= MELODY_VOICING_SALIENCE_RATIO else None
            for hz, ratio in zip(best_hz, voicing_ratio, strict=True)
        )

    return _postprocess(raw_hz)
