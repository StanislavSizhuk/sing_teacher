"""Two-level, Sakoe-Chiba-banded DTW (spec 6.7). Replaces dtw-python: its
`window_type="sakoechiba"` only *masks* a full `n x m` cost matrix rather
than allocating a banded one, so memory still scales with the product of
both sequence lengths, not with the band width -- exactly what NFR-16
forbids. This module stores only the band itself, `O(n * band)`, and the
recurrence is a plain `numba.njit` kernel (NFR-17: no per-frame Python
loops).

**Level 1 (coarse).** A fixed Sakoe-Chiba band around the literal diagonal
(`center[i] = i`) over the shared feature cache's MFCC (spec 6.9, one
frame every `FEATURES_HOP_SECONDS`). This is deliberately the same
diagonal-centered band dtw-python used, not a length-ratio-scaled one --
scaling the center would make two wildly different-length recordings
*always* reach the far corner, defeating the "diverges too far to align"
rejection this band exists to provide (spec 6.8 risk table).

**Level 2 (refine).** The coarse path is turned into a time correspondence
(`TimeMap`, reused as-is) and projected onto a much finer hop
(`PITCH_HOP_SECONDS`). A second banded pass runs with the band centered on
*that* projection instead of the diagonal, radius `ALIGN_REFINE_WINDOW_SECONDS`
-- a small, fixed-width correction on top of the coarse path, still
`O(n_fine * refine_band)` regardless of track length. This is what the
final alignment (and every downstream `TimeMap`) is built from.
"""

from __future__ import annotations

from dataclasses import dataclass

import numba
import numpy as np

from vocalcoach.audio.timemap import TimeMap
from vocalcoach.constants import DTW_MAX_CELLS
from vocalcoach.errors import AlignmentFailed, AlignmentTooLarge


@dataclass(frozen=True)
class WarpingPath:
    """`index1[k]`/`index2[k]` is the k-th matched frame pair (into sequence
    1 and sequence 2 respectively), 0-indexed, starting at `(0, 0)` and
    monotonically non-decreasing in both -- the same contract dtw-python's
    `.index1`/`.index2` had.
    """

    index1: list[int]
    index2: list[int]
    normalized_distance: float


@numba.njit(cache=True)
def _banded_dtw_kernel(
    a: np.ndarray, b: np.ndarray, full_center: np.ndarray, band: int
) -> tuple[np.ndarray, np.ndarray]:
    """`a`: `(n, d)`, `b`: `(m, d)`. `full_center[i]` (length `n + 1`,
    `full_center[0] == 0`) is the target column around which row `i`'s band
    of width `2 * band + 1` is centered. Returns `(D, P)`, both shaped
    `(n + 1, 2 * band + 1)`: `D` the banded cost matrix (`D[i, j - (full_
    center[i] - band)]` holds the cost of matching `a[:i]` to `b[:j]`,
    `inf` outside the band or otherwise unreached), `P` the predecessor
    direction per cell (`0` diagonal, `1` up/deletion, `2` left/insertion,
    `-1` unreached).
    """
    n = a.shape[0]
    m = b.shape[0]
    width = 2 * band + 1
    d = a.shape[1]

    D = np.full((n + 1, width), np.inf, dtype=np.float64)
    P = np.full((n + 1, width), -1, dtype=np.int8)

    off0 = 0 - (full_center[0] - band)
    if 0 <= off0 < width:
        D[0, off0] = 0.0

    for i in range(1, n + 1):
        c = full_center[i]
        c_prev = full_center[i - 1]
        base_cur = c - band
        base_prev = c_prev - band
        j_lo = max(1, c - band)
        j_hi = min(m, c + band)

        for j in range(j_lo, j_hi + 1):
            off = j - base_cur

            dist = 0.0
            for k in range(d):
                diff = a[i - 1, k] - b[j - 1, k]
                dist += diff * diff
            dist = np.sqrt(dist)

            best = np.inf
            best_dir = -1

            off_diag = (j - 1) - base_prev
            if 0 <= off_diag < width and D[i - 1, off_diag] < best:
                best = D[i - 1, off_diag]
                best_dir = 0

            off_up = j - base_prev
            if 0 <= off_up < width and D[i - 1, off_up] < best:
                best = D[i - 1, off_up]
                best_dir = 1

            off_left = off - 1
            if 0 <= off_left < width and D[i, off_left] < best:
                best = D[i, off_left]
                best_dir = 2

            D[i, off] = dist + best
            P[i, off] = best_dir

    return D, P


def _backtrack(
    P: np.ndarray, full_center: np.ndarray, band: int, n: int, m: int
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    i, j = n, m
    while i > 0 and j > 0:
        pairs.append((i - 1, j - 1))
        off = j - (full_center[i] - band)
        direction = P[i, off]
        if direction == 0:
            i, j = i - 1, j - 1
        elif direction == 1:
            i, j = i - 1, j
        else:
            i, j = i, j - 1
    pairs.reverse()
    return pairs


def _guard_cell_count(n: int, band: int) -> None:
    cells = (n + 1) * (2 * band + 1)
    if cells > DTW_MAX_CELLS:
        raise AlignmentTooLarge(
            f"banded DTW would need {cells} cells (band radius {band}, {n} frames), "
            f"over the {DTW_MAX_CELLS} ceiling"
        )


def banded_dtw(
    a: np.ndarray, b: np.ndarray, band: int, *, full_center: np.ndarray | None = None
) -> WarpingPath:
    """Runs one banded DTW pass. `full_center` defaults to the literal
    diagonal (`full_center[i] = i`, level 1's fixed Sakoe-Chiba band); pass
    a custom one (level 2) to center the band on a projected path instead.
    """
    n, m = a.shape[0], b.shape[0]
    if n == 0 or m == 0:
        raise AlignmentFailed("cannot align an empty sequence")

    _guard_cell_count(n, band)

    if full_center is None:
        full_center = np.arange(n + 1, dtype=np.int64)

    final_offset = m - (full_center[n] - band)
    if not (0 <= final_offset < 2 * band + 1):
        raise AlignmentFailed(
            f"the {m}-frame reference is unreachable from the {n}-frame recording "
            f"within a band radius of {band} frames -- they diverge too far to align"
        )

    D, P = _banded_dtw_kernel(a, b, full_center, band)

    final_cost = D[n, final_offset]
    if not np.isfinite(final_cost):
        raise AlignmentFailed(
            "DTW found no warping path within the configured band -- "
            "recording and reference diverge too far in tempo/content to align"
        )

    pairs = _backtrack(P, full_center, band, n, m)
    normalized_distance = float(final_cost / len(pairs))

    return WarpingPath(
        index1=[p[0] for p in pairs],
        index2=[p[1] for p in pairs],
        normalized_distance=normalized_distance,
    )


def refine_center(
    coarse: WarpingPath,
    coarse_hop_seconds: float,
    fine_hop_seconds: float,
    n_fine: int,
    m_fine: int,
) -> np.ndarray:
    """Projects a coarse warping path onto a finer hop, for level 2's band
    center: turns the coarse path into a time correspondence (`TimeMap`,
    the same class every other stage already resamples through) and reads
    off the reference frame each fine-grained recording frame maps to.
    """
    time_map = TimeMap.from_warping_path(coarse.index1, coarse.index2, coarse_hop_seconds)
    full_center = np.zeros(n_fine + 1, dtype=np.int64)
    for i in range(1, n_fine + 1):
        user_time = (i - 1) * fine_hop_seconds
        reference_time = time_map.user_to_reference(user_time)
        center = round(reference_time / fine_hop_seconds)
        full_center[i] = min(max(center, 0), m_fine)
    return full_center
