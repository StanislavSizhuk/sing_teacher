from __future__ import annotations

import time

import numpy as np
import pytest

from vocalcoach.dsp.dtw import WarpingPath, banded_dtw, locate_start_offset_scores, refine_center
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


def test_locate_start_offset_scores_finds_the_true_offset() -> None:
    """ADR-0032: an unrelated 'intro' segment prepended to a copy of the
    query should score far worse than the true offset where the query's
    own content actually begins."""
    query = _ramp(30, dims=4)
    unrelated_intro = np.random.default_rng(0).standard_normal((20, 4)).astype(np.float32)
    haystack = np.concatenate([unrelated_intro, query], axis=0)

    scores = locate_start_offset_scores(query, haystack, max_offset=25)

    true_offset = 20
    assert np.argmin(scores) == true_offset
    assert scores[true_offset] < 1e-4
    assert scores[0] > scores[true_offset] * 10


def test_locate_start_offset_scores_marks_unreachable_offsets_as_infinite() -> None:
    query = _ramp(10, dims=3)
    haystack = _ramp(12, dims=3)

    # Offset 12 (== the haystack's own length) leaves zero haystack frames
    # for any query frame to compare against -- must be reported as
    # unreachable (inf), not silently scored against nothing.
    scores = locate_start_offset_scores(query, haystack, max_offset=12)

    assert not np.isfinite(scores[12])
    assert np.isfinite(scores[0])


def test_refine_center_tracks_a_uniform_time_offset() -> None:
    # Coarse path: reference lags 1s behind the recording, at a 0.1s coarse hop.
    coarse_hop = 0.1
    fine_hop = 0.01
    index1 = list(range(20))
    index2 = list(range(10, 30))
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


def test_T8_six_minute_recordings_align_within_memory_and_time_budget() -> None:
    """T8 (spec 15.2): DTW on two 6-minute (360s) recordings must stay
    within the banded memory bound and the align stage's time budget, and
    must not raise ALIGNMENT_TOO_LARGE. 360s at PITCH_HOP_SECONDS (10ms) is
    36,000 frames -- the same fine-hop scale `align`'s level-2 refinement
    actually runs at (spec 6.7) -- with the real ALIGN_REFINE_WINDOW_SECONDS
    (0.2s / 10ms = 20 frames) band radius. 36,001 * 41 cells is comfortably
    under DTW_MAX_CELLS (50,000,000): this is what "O(n * band), not
    O(n * m)" (NFR-16) buys -- a full 36,000 x 36,000 matrix would be
    ~1.3 billion cells.
    """
    n = 36_000  # 360s at a 10ms hop
    band = 20  # ALIGN_REFINE_WINDOW_SECONDS / PITCH_HOP_SECONDS = 0.2 / 0.01
    a = _ramp(n)
    b = _ramp(n)

    start = time.monotonic()
    path = banded_dtw(a, b, band)
    elapsed = time.monotonic() - start

    assert path.normalized_distance < 1e-3
    assert path.index1[-1] == n - 1
    assert path.index2[-1] == n - 1
    assert elapsed < 45.0, f"level-2 DTW alone budgets 45s at this scale, took {elapsed:.1f}s"


def test_T9_incompatible_six_minute_recordings_fail_honestly() -> None:
    """T9 (spec 15.2): "DTW on incompatible recordings (different songs) ->
    an honest ALIGNMENT_FAILED, not random scores." Rejection happens at
    two levels, both exercised here at 6-minute scale:

    1. `banded_dtw` itself raises when the band makes the target
       structurally unreachable -- a length/tempo divergence too large for
       any path through the corridor to exist at all, same mechanism
       `test_length_difference_beyond_band_raises_alignment_failed` checks
       at a smaller scale.
    2. Two *same-length* but musically unrelated recordings don't hit that
       structural check (a path through the band always exists when both
       sides are the same length) -- `banded_dtw` returns a real path with
       a real cost instead of raising, and it is `align.py`'s
       `ALIGN_PITCH_MAX_NORMALIZED_DISTANCE` ceiling that turns "aligned, but
       at absurd cost" into `ALIGNMENT_FAILED` (exercised end-to-end, on real
       audio/pitch, by `test_align_different_melodies_raises_alignment_failed`
       in `test_align_stage.py`). What this level can honestly assert with
       synthetic feature vectors is the *relative* separation the ceiling
       check depends on existing at all: unrelated content costs
       substantially more than identical content, at the same 6-minute
       scale as case 1.
    """
    n = 36_000

    with pytest.raises(AlignmentFailed):
        banded_dtw(_ramp(n), _ramp(n // 4), band=20)

    rng = np.random.default_rng(0)
    a = rng.standard_normal((n, 13)).astype(np.float32)
    b = rng.standard_normal((n, 13)).astype(np.float32)
    identical_cost = banded_dtw(a, a.copy(), band=20).normalized_distance
    unrelated_cost = banded_dtw(a, b, band=20).normalized_distance
    assert unrelated_cost > identical_cost * 10
