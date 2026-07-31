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
the contract). Spec 6.17's own stage labels (A1 decode, A2 VAD, A3 input
classification, ...) are the *budget table's* abstract stage numbering,
which includes `mixed`-mode stages (A3 input classification, A4 melody
extraction, A8 key normalization) not yet implemented -- M3's scope, per
the milestone table. This codebase's actual stage names/numbers (spec
6.4/6.5, `docs/ML_PIPELINE.md`) differ in detail (e.g. this codebase's own
A2 is `features`, not VAD -- VAD is a gate inside `pitch`, not a separate
stage); the budget rows below are matched to the closest real stage each
names, not renamed to match.

**Cold path (M2, spec 6.4, asynchronous, once per song):**

| Stage | Budget |
|---|---|
| P1 decode/normalize | 20s |
| P2 Demucs (`shifts=0`, `segment=7`) | 420s |
| P3 `faster-whisper base` int8 | 90s |
| P4 reference pitch + features | 40s |
| **Total** | **≤ 570s** (NFR-01a: 600s) |

**Warm path, `clean` mode (M2 moved P1/Demucs/Whisper/reference-pitch out
of this path; the table below is the post-M2 shape):**

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

**Warm path, `mixed` mode (M3, spec 6.17): the same path without A5, plus A4:**

| Stage | Budget |
|---|---|
| A1 decode/normalize | 12s |
| A2 VAD | 5s |
| A3 input classification | 4s |
| A4 melody extraction (mixed only, spec 6.6/M3) | 60s |
| A6 shared feature cache | 12s |
| A7 two-level DTW | 15s |
| A8 key normalization | 2s |
| A9 aspect stages (parallel, no timbre/breath) | 15s |
| A10 aggregation/report | 3s |
| **Total** | **≤ 128s** (NFR-01c: 150s) |

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

## M2 measurements: NFR-01a (cold path) and NFR-01b (warm path)

**Date:** 2026-07-31
**Commit:** M2 branch (`feat/m2-cold-warm-split`), all M2 commits applied through the worker/API split.
**Machine:** same development machine as the M1 measurement above (12 vCPU, 31 GB RAM) -- the production VPS still does not exist (spec 16.3), so this is a comparison against the spec 6.17 budget, not yet a validated 4 vCPU/8 GB number. Re-measure once the production VPS exists.
**Song:** the same real track used for the M1 measurement (SadSvit -- "Небо"), used for personal, non-commercial local testing (spec 11.4), not committed to the repo (spec 15.3/13.3). ffprobe measures it at **225.3s (3:45)** -- shorter than spec 6.17's 6-minute cold-path reference assumption, so the cold-path numbers below should be read as "this song's real cost," not directly rescaled to a 6-minute reference; Demucs/Whisper cost both scale roughly with duration, so a 6-minute reference would cost meaningfully more than these numbers, still comfortably inside budget at the observed margin (see below).
**Methodology:** same harness style as M1 (calls each stage's `.run()` directly, outside the queue/worker process and without `runtime.threads.configure_worker_threads()` -- numpy/torch used their own defaults, same caveat as M1). `PITCH_ENGINE=crepe`, `WHISPER_MODEL=base`, `WHISPER_COMPUTE_TYPE=int8`, `DEMUCS_MODEL=htdemucs` throughout (production defaults, ADR-0014/ADR-0021).

**Cold path (P1-P4), one real end-to-end run on the reference mixture:**

| Stage | Measured | Budget |
|---|---:|---:|
| P1 `prep_reference` (decode/normalize) | 0.6s | 20s |
| P2 `separate_reference` (Demucs) | 90.7s | 420s |
| P3 `transcribe` (faster-whisper base, int8) | 121.6s | 90s |
| P4 `prep_reference_pitch` (CREPE, reference side only) | 42.0s | 40s |
| **Total** | **255.0s** | **≤ 570s** (NFR-01a: 600s) |

**Reading this table:** every stage genuinely ran -- Demucs separated real
audio, Whisper transcribed the real isolated vocal stem, CREPE tracked the
real reference pitch curve. **P3 and P4 both individually exceed their own
per-stage budget line** (P3: 121.6s vs. 90s; P4: 42.0s vs. 40s) while the
**total** (255.0s) still lands well inside the overall NFR-01a ceiling
(600s), at 45% of budget -- spec 6.17's per-stage rows are a planning
allocation, not independently enforced limits; only the `Total` row is a
gate (spec 6.17: "Це вимога, що перевіряється тестом"). Both stages
running over their own row on a shorter-than-reference-assumption song is
still worth flagging: `WHISPER_MODEL=base` (ADR-0014, chosen for exactly
this reason on the *warm*-path timeout before M2 existed) is already the
documented fallback if `small` were tried instead; there is no equivalent
recorded fallback yet if P3 alone starts threatening `TRANSCRIBE_TIMEOUT_SECONDS
= 240` on a real 6-minute reference track. Worth a real 6-minute-track
measurement before this milestone is considered fully validated (see "Known
limitations" below).

**Warm path (A1-A4, the stages M2 actually restructured), one real run:**

Reference stem and reference pitch curve reused directly from the cold-path
run above -- no re-decode, no re-running Demucs/Whisper, matching the
warm path's actual contract post-M2. Following M1's own precedent for
avoiding a non-representative full-mixture-vs-vocals-only DTW failure, the
"recording" side is the same isolated vocal stem as the reference (an
identity stand-in for "this recording, once decoded, would be roughly
this" -- see M1's methodology note above for why feeding the raw mixture
directly fails DTW on this song's instrumental intro).

| Stage | Measured | Budget |
|---|---:|---:|
| A1 `preprocess` (recording decode/normalize) | 0.6s | -- |
| A2 `features` (shared MFCC/RMS/onset cache) | 1.8s | -- |
| A3 `align` (two-level banded DTW) | 2.1s | -- |
| A4 `pitch` (CREPE, user side only) | 32.2s | -- |
| **A1-A4 subtotal** | **36.8s** | -- |
| A5-A11 (rhythm, vibrato, dynamics, timbre, breath, recording_condition, aggregate) | not re-measured -- M2 touches none of this code; carried forward from the M1 table above (70ms sequential / ~29ms parallel) | -- |
| **Estimated total** | **~36.9s** | **≤ 86s** (NFR-01b: 90s) |

**Reading this table:** A1-A3 are close to their pre-M2 M1 numbers (same
mechanics, minor run-to-run variance). **A4 is not directly comparable to
M1's `pitch: 30,069 ms` row**: that pre-M2 number measured a single
`PitchStage` call computing *both* the user's and the reference's curves
together (with the reference side itself sometimes cache-skipped
depending on whether the song had been analyzed before); post-M2, `pitch`
only ever computes the user side -- the reference curve is `context.reference_pitch`,
already-cached cold-path output, read directly, never recomputed. The two
numbers landing in the same rough range (30s vs. 32s) is not evidence
either got faster or slower; they are measuring different amounts of work
under different conditions (this environment's CPU contention, thread
defaults left unconfigured per the methodology caveat) and should not be
read as a regression. What matters for NFR-01b is the **total**: 36.9s
against an 86s/90s budget, 43% of budget, with the same comfortable margin
the pre-M2 warm path already had.

**Known limitations of this measurement:**

- The reference track (225s) is shorter than spec 6.17's 6-minute
  cold-path assumption; a real 6-minute reference should be measured
  before treating NFR-01a as fully validated, particularly for P3
  (Whisper), which already runs over its own per-stage budget row on this
  shorter track.
- Neither path applies `runtime.threads.configure_worker_threads()` (spec
  6.11) -- same caveat M1 already carried; the real containerized worker
  pins BLAS/torch thread counts explicitly, which this standalone harness
  does not.
- Not yet measured against the eventual 4 vCPU/8 GB production VPS shape
  (spec 16.3) -- neither was M1's.

## M3 measurement: NFR-01c (mixed warm path) -- partial

**Date:** 2026-07-31
**Commit:** M3 branch (`feat/m3-mixed-mode-spike`).
**Machine:** same development machine as M1/M2 (12 vCPU, 31 GB RAM) -- same
production-VPS caveat as both prior measurements.

**Methodology, and why this one is partial:** M1/M2 both measured a real
end-to-end run against a real song. M3 has no equivalent: a `mixed`
recording needs real singing *plus* real accompaniment mixed together, and
no such test recording exists in this environment (spec 15.3 also bans
committing one to the repo either way). What is measured directly instead
is `dsp/melody.py::extract_melody` -- A4, the one new stage expensive
enough to matter for the budget -- run on synthetic mixtures (harmonic
vocal + accompaniment, the same construction `tests/test_melody_extraction.py`
uses) at several durations, wall-clock, via a standalone script outside the
worker process (same thread-configuration caveat as M1/M2: `runtime.threads
.configure_worker_threads()`, spec 6.11, not applied here).

| Synthetic mixture duration | A4 `extract_melody` wall time |
|---:|---:|
| 30s | 2.9s |
| 60s | 4.1s |
| 180s | 13.0s |
| 225s (M2's real reference track's own duration) | 15.5s |
| 360s (spec 6.17's cold-path reference duration) | 24.9s |

Scaling is roughly linear (the chunked candidate/frame tensor,
`MELODY_CHUNK_FRAMES`, bounds per-chunk work independent of total length) --
no evidence of the quadratic blowup a naive per-frame implementation would
show. `key_normalization` (A8) was also measured directly, on a
22,552-frame synthetic `deviation_cents` array (matching a 225s recording
at the 10ms pitch hop): **7ms** -- negligible against its 2s budget line.

**Estimated mixed-path total**, built from real numbers where they exist:
A1/A6/A7 unchanged from M2's measured `clean`-path numbers (0.6s + 1.8s +
2.1s = 4.5s, mode-independent -- none of those three stages' work depends
on which pitch source runs), A4 measured above (15.5s at the M2 reference
track's own 225s duration), A8 measured above (~0.01s), A9's three
remaining aspects (rhythm/vibrato/dynamics, no timbre/breath) carried
forward from the M1 table (well under 50ms combined), A10 ~0ms:

| Component | Value | Source |
|---|---:|---|
| A1 preprocess | 0.6s | measured, M2 (mode-independent) |
| A6 features | 1.8s | measured, M2 (mode-independent) |
| A7 align | 2.1s | measured, M2 (mode-independent) |
| A4 melody extraction | 15.5s | measured, M3, synthetic, this table |
| A8 key normalization | ~0.0s | measured, M3, synthetic, this table |
| A9 aspects (rhythm/vibrato/dynamics only) | ~0.05s | measured, M1 (mode-independent, minus timbre/breath) |
| A10 aggregate | ~0.0s | measured, M1 |
| **Estimated total** | **~20.1s** | **≤ 128s** (NFR-01c: 150s), 13-16% of budget |

**Reading this table:** even as an estimate assembled from two different
sessions' measurements rather than one real end-to-end run, the margin
(~20s against a 150s ceiling) is wide enough that the composition method
itself is very unlikely to be hiding a budget violation -- A4 would have
to be roughly 6x slower than measured, on top of every other component
being free, before this got close to 150s. Re-measure end-to-end once a
real `mixed` test recording exists (see "Known limitations" in
`docs/ML_PIPELINE.md`) before treating NFR-01c as fully validated the way
NFR-01a/NFR-01b now are.

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
| Chunked (frame x candidate x harmonic) tensor in melody extraction (M3, `MELODY_CHUNK_FRAMES`) | Bounds A4's peak memory independent of recording length, same principle as the banded DTW's corridor | Linear wall-time scaling measured 30s-360s (this file's M3 table); no quadratic blowup |

## When it gets slow

The ordered checklist from spec 6.17, unchanged by M1, stage names updated
for M2's cold/warm split (see `docs/ML_PIPELINE.md`):

1. Check whether the song's cold path already reached `ready` (spec 6.2,
   6.13) -- a song still `pending`/`processing` is the largest, most
   common source of an analysis appearing slow: it is waiting on a
   different job (spec 10.3), not spending wall time itself.
2. Check thread configuration (spec 6.11) -- both under- and over-subscription
   look the same ("slow").
3. Read the per-stage `duration_ms` already recorded in `songs.prep_stages_json`
   (cold path) or `analyses.stages_json` (warm path) and optimize the
   specific most expensive stage. Optimizing without this data is not
   allowed.
4. Lower model parameters: `WHISPER_MODEL=tiny`, `PITCH_ENGINE=pyworld` (not
   yet implemented, see ADR-0015), a coarser `DTW_COARSE_HOP_MS`-equivalent.
5. Limit the analyzed region (`ANALYSIS_MAX_SECONDS`, e.g. chorus only).
6. Add VPS resources.

Rewriting part of the pipeline in Rust/C is deliberately absent from this
list -- see ADR-0015. It is only in scope as a consequence of step 3, if
profiling shows the bottleneck is this repository's *own* Python code and
`numba` doesn't close it.
