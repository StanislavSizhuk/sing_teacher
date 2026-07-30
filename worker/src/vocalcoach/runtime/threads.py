"""Thread/BLAS configuration, applied once at process start (spec 6.11,
NFR-18): every BLAS/OpenMP thread count is set explicitly from config
before numpy or torch is ever imported. Both libraries size their thread
pool from these env vars at import/first-use time, so setting them any
later has no effect -- this must run before `vocalcoach.worker` (and
everything it transitively imports: librosa, torch, demucs, torchcrepe)
is ever imported.
"""

from __future__ import annotations

import os
from pathlib import Path

# Every env var numpy's BLAS backend and related libraries read their
# thread pool size from (spec 6.11's explicit list).
_THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)

_CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


def _cgroup_cpu_limit() -> int | None:
    """Effective vCPU allotment from the container's cgroup CPU quota, or
    `None` if unset/unlimited/unreadable. `os.cpu_count()` reports the
    *host's* core count regardless of a compose `deploy.resources.limits.cpus`
    quota -- the kernel enforces that quota without changing what
    `os.cpu_count()` returns, so the container's actual allotment must be
    read from cgroup files directly (spec 6.11).
    """
    try:
        if _CGROUP_V2_CPU_MAX.exists():
            quota_str, period_str = _CGROUP_V2_CPU_MAX.read_text().split()
            if quota_str == "max":
                return None
            quota, period = int(quota_str), int(period_str)
        elif _CGROUP_V1_QUOTA.exists() and _CGROUP_V1_PERIOD.exists():
            quota = int(_CGROUP_V1_QUOTA.read_text())
            period = int(_CGROUP_V1_PERIOD.read_text())
            if quota <= 0:
                return None
        else:
            return None
    except (OSError, ValueError):
        return None

    if period <= 0:
        return None
    return max(1, quota // period)


def _detect_cpu_count() -> int:
    limit = _cgroup_cpu_limit()
    if limit is not None:
        return limit
    return os.cpu_count() or 1


def configure_worker_threads() -> int:
    """Resolves the worker's thread count and applies it to every BLAS env
    var, plus normalizes `WORKER_CPU_THREADS` itself in `os.environ` to the
    resolved value (0 meaning "autodetect" becomes the concrete number) so
    `Settings.worker_cpu_threads`, parsed later, reports what was actually
    applied rather than the raw "0" input.

    Returns the resolved thread count; the caller logs it once logging
    itself is configured (this runs too early in process startup for
    `logging` to be set up yet).
    """
    raw = os.environ.get("WORKER_CPU_THREADS", "0").strip()
    try:
        configured = int(raw)
    except ValueError:
        configured = 0
    threads = configured if configured > 0 else _detect_cpu_count()

    for var in (*_THREAD_ENV_VARS, "WORKER_CPU_THREADS"):
        os.environ[var] = str(threads)

    return threads
