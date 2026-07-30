"""Entrypoint for `python -m vocalcoach`."""

from vocalcoach.runtime.threads import configure_worker_threads

# Spec 6.11/NFR-18: must run before numpy/torch are ever imported, which
# `from vocalcoach.worker import run` below pulls in transitively (via the
# pipeline stages) -- both size their thread pool from these env vars at
# import time, not when read later.
configure_worker_threads()

from vocalcoach.worker import run  # noqa: E402 -- see comment above

if __name__ == "__main__":
    run()
