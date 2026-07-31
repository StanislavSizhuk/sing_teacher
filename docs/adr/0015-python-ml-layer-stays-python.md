# ADR-0015: ML layer stays Python; optimize via runtimes/algorithms, not a Rust/C rewrite

- Status: Accepted
- Date: 2026-07-30

## Context

Every prior performance complaint about the pipeline ("too slow", the
`TIMEOUT` bug fixed by ADR-0014) invites the same reflexive fix: rewrite
the worker in a compiled language. Before starting M1 (spec 18), this needs
a real answer, because the M1 acceptance criteria depend on it -- if the
answer were "rewrite in Rust", none of NFR-16/17/18 would be about Python
at all.

Profiling the actual v1.0 pipeline (before any M1 change, `docs/PERFORMANCE.md`
"before" column) on a real 225s song: 64.4s total warm-path wall time, of
which pitch detection (CREPE, native C++/PyTorch kernels via `torchcrepe`)
is 38.8s (60%), transcription (Whisper, native CTranslate2/PyTorch kernels)
is 22.9s (36%), and every stage that is *actually* Python control flow
(`align`, `rhythm`, `vibrato`, `dynamics`, `timbre`, `breath`,
`recording_condition`, `aggregate` combined) is under 1.8s (3%). Demucs
separation (native PyTorch) is excluded from this warm-path figure but is
the single largest cold-path cost by far. In other words: essentially all
wall time already executes inside native kernels (PyTorch, CTranslate2,
`librosa`'s own C/numba-jitted internals); the Python this repository owns
is a few hundred lines of orchestration and comparison logic that measures
in milliseconds already.

## Decision

The ML worker stays Python. Performance work targets three levers, in this
order, matching spec 6.17's own prescribed sequence: (1) caching (spec 6.9 --
stop recomputing the same MFCC/RMS twice), (2) runtime substitution (ADR-0021's
faster-whisper; `pyworld` deferred, see below) and algorithmic bounding
(ADR-0017's banded DTW), (3) explicit thread/parallelism configuration
(spec 6.10/6.11). None of these require a language change; `numba.njit`
(already a dependency, now used directly for the DTW kernel) gives compiled-
loop performance for the one piece of code that was ever a real per-frame
Python loop, without a second toolchain, a second CI matrix, or a second
language for a solo developer to carry (spec NFR-14, "bus factor = 1").

## Consequences

- Every M1 optimization (feature cache, banded DTW, VAD gate, parallel
  aspects, thread config) ships as ordinary Python + `numba`/`numpy`, in the
  existing Docker image and CI pipeline.
- A future genuine hot loop discovered by profiling (not guessed) is still
  free to reach for `numba` first, a native extension second; a full
  rewrite is not on the table absent a profiler pointing at *this
  repository's own* code, not a third-party kernel, as the bottleneck.
- `pyworld` (spec 6.6's proposed default pitch engine) is explicitly **not**
  part of M1 -- it is a model/algorithm swap, not a perf-critical-path
  change the measurements above call for, and M1's own acceptance list
  (spec 18) does not name it. `PITCH_ENGINE` stays `crepe`/`pyin`; revisit
  with its own ADR if a future measurement shows it matters.

## Alternatives considered

- **Rewrite hot stages in Rust/C via PyO3/ctypes** -- rejected per the
  profiling above: the hot path is already native code this repository
  doesn't own; rewriting the 3% that is Python buys close to nothing while
  adding a second toolchain to the image, CI, and the one developer
  maintaining both.
- **Move the whole worker to Go** (matching `api/`) -- rejected: Go has no
  equivalent ecosystem for `librosa`/Demucs/Whisper/CREPE; the entire
  pipeline would need reimplementing from scratch, with no source of
  algorithmic truth to check the port against.
