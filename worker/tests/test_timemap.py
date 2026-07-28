from __future__ import annotations

import numpy as np

from vocalcoach.audio.timemap import TimeMap


def test_identity_mapping_round_trips() -> None:
    # index1[i] == index2[i] for every i -- the two timelines are identical.
    time_map = TimeMap.from_warping_path(list(range(100)), list(range(100)), hop_seconds=0.05)
    assert time_map.user_to_reference(1.0) == 1.0
    assert time_map.reference_to_user(2.5) == 2.5


def test_from_align_stage_data_matches_from_warping_path() -> None:
    data = {"index1": [0, 1, 2], "index2": [0, 2, 4], "hop_seconds": 0.1}
    time_map = TimeMap.from_align_stage_data(data)
    assert time_map.user_to_reference(0.1) == 0.2


def test_user_to_reference_stretches_when_user_is_slower() -> None:
    # User took twice as long to sing the same content as the reference.
    time_map = TimeMap.from_warping_path([0, 2, 4], [0, 1, 2], hop_seconds=0.1)
    assert time_map.user_to_reference(0.2) == 0.1
    assert time_map.user_to_reference(0.4) == 0.2


def test_resample_reference_onto_user_grid_picks_mapped_values() -> None:
    time_map = TimeMap.from_warping_path([0, 1, 2], [0, 1, 2], hop_seconds=1.0)
    user_series = np.array([0.0, 0.0, 0.0])
    reference_series = np.array([10.0, 20.0, 30.0])
    aligned = time_map.resample_reference_onto_user_grid(
        user_series, reference_series, hop_seconds=1.0
    )
    np.testing.assert_array_equal(aligned, [10.0, 20.0, 30.0])


def test_resample_reference_onto_user_grid_clamps_out_of_range() -> None:
    # user runs longer than the mapping covers; the mapping still returns a
    # value (np.interp clamps to the edge) so this must not index-crash.
    time_map = TimeMap.from_warping_path([0, 1], [0, 1], hop_seconds=1.0)
    user_series = np.zeros(5)
    reference_series = np.array([5.0, 6.0])
    aligned = time_map.resample_reference_onto_user_grid(
        user_series, reference_series, hop_seconds=1.0
    )
    assert len(aligned) == 5
    assert aligned[-1] == 6.0
