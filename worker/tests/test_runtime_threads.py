from __future__ import annotations

import os
from pathlib import Path

import pytest

from vocalcoach.runtime import threads


@pytest.fixture(autouse=True)
def _clear_thread_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (*threads._THREAD_ENV_VARS, "WORKER_CPU_THREADS"):
        monkeypatch.delenv(var, raising=False)
    # Never let the test host's own cgroup limits (this CI runner, a dev
    # container, ...) leak into a test that expects the plain os.cpu_count()
    # fallback -- each test that wants autodetection opts into a specific
    # cgroup file itself.
    monkeypatch.setattr(threads, "_CGROUP_V2_CPU_MAX", Path("/nonexistent-cgroup-v2"))
    monkeypatch.setattr(threads, "_CGROUP_V1_QUOTA", Path("/nonexistent-cgroup-v1-quota"))
    monkeypatch.setattr(threads, "_CGROUP_V1_PERIOD", Path("/nonexistent-cgroup-v1-period"))


def test_explicit_worker_cpu_threads_wins_over_autodetect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_CPU_THREADS", "3")

    resolved = threads.configure_worker_threads()

    assert resolved == 3
    for var in threads._THREAD_ENV_VARS:
        assert os.environ[var] == "3"


def test_zero_or_unset_autodetects_from_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_CPU_THREADS", "0")
    monkeypatch.setattr(os, "cpu_count", lambda: 7)

    resolved = threads.configure_worker_threads()

    assert resolved == 7
    assert os.environ["WORKER_CPU_THREADS"] == "7"


def test_cgroup_v2_quota_caps_below_host_cpu_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("200000 100000\n")  # 2 vCPUs worth of quota
    monkeypatch.setattr(threads, "_CGROUP_V2_CPU_MAX", cpu_max)
    monkeypatch.setattr(os, "cpu_count", lambda: 16)

    resolved = threads.configure_worker_threads()

    assert resolved == 2


def test_cgroup_v2_max_quota_means_unlimited_falls_back_to_cpu_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("max 100000\n")
    monkeypatch.setattr(threads, "_CGROUP_V2_CPU_MAX", cpu_max)
    monkeypatch.setattr(os, "cpu_count", lambda: 5)

    assert threads.configure_worker_threads() == 5


def test_cgroup_v1_quota_and_period_are_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quota = tmp_path / "cfs_quota_us"
    period = tmp_path / "cfs_period_us"
    quota.write_text("400000\n")
    period.write_text("100000\n")
    monkeypatch.setattr(threads, "_CGROUP_V1_QUOTA", quota)
    monkeypatch.setattr(threads, "_CGROUP_V1_PERIOD", period)
    monkeypatch.setattr(os, "cpu_count", lambda: 16)

    assert threads.configure_worker_threads() == 4


def test_garbage_worker_cpu_threads_falls_back_to_autodetect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_CPU_THREADS", "not-a-number")
    monkeypatch.setattr(os, "cpu_count", lambda: 8)

    assert threads.configure_worker_threads() == 8
