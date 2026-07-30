from __future__ import annotations

import numpy as np
import pytest

from vocalcoach.dsp.dtw import banded_dtw, refine_center
from vocalcoach.errors import AlignmentFailed, AlignmentTooLarge


def _ramp(n: int, dims: int = 3) -> np.ndarray:
    """A smooth, monotonic feature sequence -- easy to align to itself or a
    time-shifted copy of itself."""
    t = np.linspace(0.0, 1.0, n)
    return np.stack([np.sin(t * (k + 1) * np.pi) for k in range(dims)], axis=1).astype(np.float32)


def test_identical_sequences_align_on_the_diagonal_with_near_zero_cost() -> None:
    a = _ramp(50)
    path = banded_dtw(a, a.copy(), band=5)

    assert path.index1[0] == 0
    assert path.index2[0] == 0
    assert path.index1[-1] == 49
    assert path.index2[-1] == 49
    assert path.normalized_distance < 1e-4
    consecutive = zip(path.index1, path.index1[1:], strict=False)
    assert all(a <= b for a, b in consecutive)


def test_random_unrelated_sequences_cost_much_higher_than_identical() -> None:
    rng = np.random.default_rng(0)
    a = rng.standard_normal((60, 4)).astype(np.float32)
    b = rng.standard_normal((60, 4)).astype(np.float32)

    identical_cost = banded_dtw(a, a.copy(), band=10).normalized_distance
    unrelated_cost = banded_dtw(a, b, band=10).normalized_distance

    assert unrelated_cost > identical_cost * 10


def test_length_difference_beyond_band_raises_alignment_failed() -> None:
    a = _ramp(10)
    b = _ramp(200)

    with pytest.raises(AlignmentFailed):
        banded_dtw(a, b, band=5)


def test_empty_sequence_raises_alignment_failed() -> None:
    a = _ramp(10)
    empty = np.zeros((0, 3), dtype=np.float32)

    with pytest.raises(AlignmentFailed):
        banded_dtw(a, empty, band=5)


def test_oversized_band_raises_alignment_too_large() -> None:
    a = _ramp(1000)
    with pytest.raises(AlignmentTooLarge):
        banded_dtw(a, a.copy(), band=10_000_000)


def test_memory_is_bounded_by_band_not_by_the_full_product() -> None:
    """NFR-16: the banded matrix must be O(n * band), not O(n * m) -- a very
    long pair of sequences with a small band must not be rejected by the
    DTW_MAX_CELLS guard, which a full n*m matrix would be nowhere near.
    """
    n = 20_000
    a = _ramp(n)
    b = _ramp(n)
    path = banded_dtw(a, b, band=50)
    assert path.normalized_distance < 1e-3


def test_refine_center_tracks_a_uniform_time_offset() -> None:
    # Coarse path: reference lags 1s behind the recording, at a 0.1s coarse hop.
    coarse_hop = 0.1
    fine_hop = 0.01
    index1 = list(range(20))
    index2 = list(range(10, 30))
    from vocalcoach.dsp.dtw import WarpingPath

    coarse = WarpingPath(index1=index1, index2=index2, normalized_distance=0.0)

    full_center = refine_center(coarse, coarse_hop, fine_hop, n_fine=200, m_fine=300)

    # At fine frame 0 (t=0s), the reference should sit near coarse's t=1s
    # mark, i.e. fine frame ~100.
    assert 95 <= full_center[1] <= 105
    # At fine frame 100 (t=1s), reference should be near t=2s -> frame ~200.
    assert 195 <= full_center[101] <= 205


def test_refine_center_last_row_lands_within_a_narrow_band_of_m_fine() -> None:
    """Regression: a coarse path's nominal time coverage
    (`n_coarse * coarse_hop`) and a fine sequence's (`n_fine * fine_hop`)
    don't line up exactly (`librosa`'s frame-count rounding differs per
    hop), so the naive per-frame projection clamped flat for the whole
    tail once `user_time` ran past the coarse path's own covered range --
    every trailing fine frame collapsed onto the same center and fell
    outside a real DTW refine band (spec 6.7's whole point). This measured
    ~42 frames of drift on a real ~207s song; a synthetic identity path
    long enough to exhibit the same rounding gap must still land the last
    row within a realistic refine band of the true endpoint.
    """
    from vocalcoach.dsp.dtw import WarpingPath

    n_coarse = 4142  # matches the real measurement that caught this bug
    identity = list(range(n_coarse))
    coarse = WarpingPath(index1=identity, index2=identity, normalized_distance=0.0)
    coarse_hop, fine_hop = 0.05, 0.01
    n_fine = m_fine = 20748

    full_center = refine_center(coarse, coarse_hop, fine_hop, n_fine=n_fine, m_fine=m_fine)

    band = 20
    assert abs(int(full_center[n_fine]) - m_fine) <= band
    assert full_center[n_fine] == m_fine
