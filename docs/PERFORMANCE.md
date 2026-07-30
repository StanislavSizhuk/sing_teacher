# Performance

## Reference hardware

Spec 16.3 names 4 vCPU / 8 GB RAM (the production VPS shape) as the
reference for the spec 6.17 budget table below, but no production VPS is
provisioned yet (spec 16.3: "немає провіженого сервера"). The M1 before/after
measurements in this document were taken on the current development
machine instead: **12 vCPU, 31 GB RAM**, same machine and same conditions
for both runs, so the *comparison* (before vs. after) is valid even though
neither run is yet validated against the eventual 4 vCPU/8 GB target.
Re-measure once the production VPS exists and update this section.

The benchmark harness used (`worker/tests`' equivalent, run as a standalone
script outside the queue/worker process) calls each stage's `.run()`
directly and does **not** go through `python -m vocalcoach`'s entrypoint,
so `runtime.threads.configure_worker_threads()` (spec 6.11) was not
applied for these specific runs -- numpy/torch used their own defaults.
The 6.11 thread-pinning behavior itself is covered by
`tests/test_runtime_threads.py`, not by these wall-clock numbers.

## Budget

Reproduced from spec 6.17 (this file tracks measurements; the spec holds
the contract). M1 does not restructure cold/warm path (that is M2), so
these budgets are the eventual target this milestone works toward, not
something M1 itself is measured against directly yet.

**Warm path, `clean` mode:**

| Stage | Budget |
|---|---|
| A1 decode/normalize | 12s |
| A2 VAD | 5s |
| A3 input classification | 4s |
| A5 user pitch (voiced frames only) | 18s |
| A6 shared feature cache | 12s |
| A7 two-level DTW | 15s |
| A8 key normalization | 2s |
| A9 aspect stages (parallel) | 15s |
| A10 aggregation/report | 3s |
| **Total** | **≤ 86s** (NFR-01b: 90s) |

## Latest measurements

**Date:** 2026-07-30
**Commit:** `9032f17` (branch `perf/m1-pipeline-performance`, all M1 commits applied)
**Song:** a real ~207s (3:27) track (SadSvit -- "Небо"), used for personal,
non-commercial local testing (spec 11.4). Not committed to the repo (spec
15.3/13.3: no real audio fixtures in git).

**Methodology:** `align` compares the recording's own MFCC against the
reference's *isolated vocal stem* MFCC (spec ADR-0003 -- only the reference
ever goes through Demucs). Feeding the same full band-mix file as both
"recording" and "reference" is therefore not representative (full mix vs.
vocals-only content diverges enough locally to fail DTW outright on a song
with an instrumental intro -- an artifact of the harness, not a product
bug). So: Demucs ran once, offline, unmeasured, to extract this song's real
isolated vocal stem; every measured stage below runs on that vocal-only
file for *both* "recording" and "reference" (a fake, identity
`separate_reference` stands in for the already-cached stem this
represents). Demucs' own cost is unaffected by M1 and excluded from the
comparison. `PITCH_ENGINE=crepe`, `WHISPER_MODEL=base` throughout, matching
production defaults (ADR-0014).

| Stage | Before (v1.0) | After (M1) | Δ |
|---|---:|---:|---:|
| preprocess | 712 ms | 679 ms | -5% |
| separate_reference (identity, benchmark stand-in) | 178 ms | 208 ms | +17% (noise) |
| features (new, spec 6.9) | -- | 1,171 ms | new stage |
| transcribe | 22,890 ms | 11,266 ms | **-51%** |
| align | 751 ms | 759 ms | ~0% (now 2 DTW passes, same wall time) |
| pitch | 38,847 ms | 30,069 ms | **-23%** |
| rhythm | 396 ms | 14 ms | **-96%** |
| vibrato | 19 ms | 12 ms | -37% |
| dynamics | 109 ms | 9 ms | **-92%** |
| timbre | 254 ms | 29 ms | **-89%** |
| breath | 104 ms | 3 ms | **-97%** |
| recording_condition | 122 ms | 3 ms | **-98%** |
| aggregate | 0 ms | 0 ms | -- |
| **Total wall (sequential aspects)** | **64,391 ms** | **44,238 ms** | **-31%** |
| Total wall, aspects run in parallel (measured group sum -> max) | n/a (v1.0 had no parallel path) | 44,200 ms | aspect group: 67 ms sequential -> 29 ms parallel |

**Reading this table:**

- **transcribe** (-51%): `faster-whisper`'s CTranslate2 int8 runtime
  replacing `openai-whisper` (ADR-0021).
- **pitch** (-23%): the VAD gate (ADR-0023) skips CREPE over silent spans.
  This vocal stem has relatively few/short pauses (it is a continuously-sung
  take); a recording with more natural phrase gaps would show a larger
  reduction from the same mechanism.
- **rhythm/dynamics/timbre/breath/recording_condition** (-89% to -98%):
  the shared feature cache (spec 6.9). These stages used to each open the
  audio and run their own `librosa` MFCC/RMS/onset extraction; now they
  read already-computed arrays out of the `features` stage's `.npz`. The
  cost didn't disappear, it moved into `features` (1,171 ms, one MFCC + two
  RMS passes + onset detection, per side) -- and stopped being paid twice
  (align/timbre both wanted the same MFCC; dynamics/breath both wanted the
  same RMS envelope).
- **align** (~0%): now runs *two* banded DTW passes (coarse + fine
  refinement, spec 6.7) instead of one `dtw-python` call, at roughly the
  same wall time -- the banding is what keeps a strictly more thorough
  alignment from costing more, not less thorough work at the same cost.
- **Parallel aspects**: on this song, the aspect stages are already so
  cheap after the shared-cache win (67 ms total, sequential) that
  parallelizing them saves only ~38 ms in absolute terms here. The
  mechanism (spec 6.10) is verified separately by T13
  (`tests/test_pipeline_parallel_consistency.py`) for score-correctness,
  and matters more in absolute terms on a machine with fewer cores per
  stage or aspect stages with a heavier synthetic-fixture-scale workload
  than this measurement's already-optimized baseline.

## Optimisation log

| Change | Why | Measured effect |
|---|---|---|
| Explicit `WORKER_CPU_THREADS`/BLAS thread config (6.11) | NFR-18: no library defaults | Not directly wall-clock-visible in this harness (see caveat above); prevents thread oversubscription in the real containerized deployment |
| Shared MFCC/RMS/onset feature cache (6.9) | `align`+`timbre` and `dynamics`+`breath` each recomputed the same representation | rhythm/dynamics/timbre/breath/recording_condition combined: 985 ms -> 70 ms |
| VAD gate on pitch detection (6.5, ADR-0023) | CREPE ran over silence it could only report `None` for anyway | pitch: 38.8s -> 30.1s (-23%) |
| Own two-level banded DTW (6.7, ADR-0017) | `dtw-python`'s window masked a full `n*m` matrix, not a banded one (NFR-16 violation); fixed a real tail-extrapolation bug found by this same benchmark | align: ~same wall time for strictly more work (2 passes instead of 1); memory now `O(n*band)` |
| Parallel aspect stages (6.10) | Independent stages ran sequentially | T13-verified score parity; wall-clock benefit scales with per-stage cost and available cores |
| `faster-whisper` runtime (ADR-0021) | `openai-whisper`'s float32 PyTorch inference | transcribe: 22.9s -> 11.3s (-51%) |
| Dense curves as `bytea` (7.3, ADR-0022) | JSONB text for ~18k-point float arrays, stored redundantly in `stages_json` | Not wall-clock (storage/IO, not measured here); removes duplicate storage of the reference/user pitch curves |

## When it gets slow

The ordered checklist from spec 6.17, unchanged by M1:

1. Check whether the reference cache is hitting (spec 6.13) -- a cache miss
   is the largest, most common source of slowdown.
2. Check thread configuration (spec 6.11) -- both under- and over-subscription
   look the same ("slow").
3. Read `stage_durations_json` and optimize the specific most expensive
   stage. Optimizing without this data is not allowed.
4. Lower model parameters: `WHISPER_MODEL=tiny`, `PITCH_ENGINE=pyworld` (not
   yet implemented, see ADR-0015), a coarser `DTW_COARSE_HOP_MS`-equivalent.
5. Limit the analyzed region (`ANALYSIS_MAX_SECONDS`, e.g. chorus only).
6. Add VPS resources.

Rewriting part of the pipeline in Rust/C is deliberately absent from this
list -- see ADR-0015. It is only in scope as a consequence of step 3, if
profiling shows the bottleneck is this repository's *own* Python code and
`numba` doesn't close it.
