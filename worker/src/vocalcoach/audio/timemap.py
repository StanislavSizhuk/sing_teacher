"""Turns a stage 4 DTW warping path into a user-time <-> reference-time
mapping, reused by every later stage that needs to compare the two signals
at corresponding moments (spec 6.3.6, 6.3.8, 6.3.10: "after DTW alignment").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class TimeMap:
    """Piecewise-linear, monotonic mapping built from the DTW correspondence
    points `(index1, index2)` stage 4 produced, each pair `hop_seconds`
    apart on its own timeline.
    """

    user_times: np.ndarray
    reference_times: np.ndarray

    @classmethod
    def from_warping_path(
        cls, index1: list[int], index2: list[int], hop_seconds: float
    ) -> TimeMap:
        return cls(
            user_times=np.asarray(index1, dtype=np.float64) * hop_seconds,
            reference_times=np.asarray(index2, dtype=np.float64) * hop_seconds,
        )

    @classmethod
    def from_align_stage_data(cls, data: dict[str, Any]) -> TimeMap:
        """Builds a `TimeMap` from `AlignStage`'s `StageResult.data`."""
        return cls.from_warping_path(data["index1"], data["index2"], data["hop_seconds"])

    def user_to_reference(self, user_time_seconds: float) -> float:
        return float(np.interp(user_time_seconds, self.user_times, self.reference_times))

    def reference_to_user(self, reference_time_seconds: float) -> float:
        # reference_times is not guaranteed strictly increasing (DTW allows
        # flat steps), but np.interp only requires its xp argument sorted,
        # which the DTW path already is by construction.
        return float(np.interp(reference_time_seconds, self.reference_times, self.user_times))

    def resample_reference_onto_user_grid(
        self, user_series: np.ndarray, reference_series: np.ndarray, hop_seconds: float
    ) -> np.ndarray:
        """Returns one reference-series value per `user_series` frame, each
        picked from the reference time this mapping says corresponds to
        that user frame -- the point-by-point comparison every later stage
        needs, despite the user's different tempo/timing (spec 6.3.6/6.3.8/6.3.10).
        """
        aligned = np.empty_like(user_series)
        last_index = len(reference_series) - 1
        for i in range(len(user_series)):
            reference_time = self.user_to_reference(i * hop_seconds)
            reference_index = round(reference_time / hop_seconds)
            aligned[i] = reference_series[min(max(reference_index, 0), last_index)]
        return aligned
