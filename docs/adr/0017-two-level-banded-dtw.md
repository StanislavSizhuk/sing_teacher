# ADR-0017: Own two-level banded DTW, replacing dtw-python

- Status: Accepted
- Date: 2026-07-30

## Context

`align` (spec 6.3.4, ADR-0004) used `dtw-python`'s `dtw()` with
`window_type="sakoechiba"` to bound the warping search. That window only
*masks* which cells of a cost matrix are eligible; `dtw-python` still
allocates the full `n x m` matrix underneath (confirmed by inspecting the
allocation path this call takes: it builds a dense `_globalCostMatrix` of
shape `(n, m)` regardless of `window_args`). Memory therefore scales with
the *product* of both sequence lengths, not with the band's width -- for a
6-minute-vs-6-minute alignment at the old 50ms hop, that is on the order of
tens of millions of cells at 8 bytes apiece even though the band itself
only ever needs `O(n * band)`. NFR-16 makes the banded bound a hard
requirement, not a target: "Реалізація без обмеження коридору не
приймається."

`dtw-python`'s recurrence also runs inside its own C extension, so nothing
about *how* it computes was under this repository's control, and its
`step_pattern="symmetric2"` cost scale is whatever that library's Cython
code produces -- not something `ALIGN_MAX_NORMALIZED_DISTANCE` could be
reasoned about independently of the library.

## Decision

Replace `dtw-python` with an own implementation (`worker/src/vocalcoach/dsp/dtw.py`):
a plain classic-DTW recurrence (`dist(i,j) + min(diag, up, left)`), stored
in a genuinely banded array of shape `(n + 1, 2 * band + 1)` addressed by
`offset = j - center(i) + band` rather than a full `(n + 1, m + 1)` matrix
-- `O(n * band)` memory, not `O(n * m)`, satisfying NFR-16 literally. The
recurrence itself is a `numba.njit(cache=True)` kernel (NFR-17: "Щільні
per-frame цикли на чистому Python... заборонено"), with a
`DTW_MAX_CELLS`-based upfront size guard (`AlignmentTooLarge`,
`ALIGNMENT_TOO_LARGE`) that refuses to even start a pathological input.

Two levels (spec 6.7):

1. **Coarse**: the shared feature cache's MFCC (spec 6.9, `FEATURES_HOP_SECONDS`
   = 50ms), banded around the literal diagonal (`center(i) = i`), radius
   `ALIGN_WINDOW_SECONDS` (10s) -- deliberately *not* scaled by the two
   sequences' length ratio, which would make any two recordings reach the
   far corner regardless of how much they actually diverge, defeating the
   "too different to align" rejection this band exists to provide (spec
   6.8 risk table, T9).
2. **Refine**: a second banded pass at `PITCH_HOP_SECONDS` (10ms), centered
   on the coarse path projected through `TimeMap` (already the class every
   other stage resamples through) instead of the diagonal, radius
   `ALIGN_REFINE_WINDOW_SECONDS` (200ms) -- a small, fixed-width correction,
   still `O(n_fine * refine_band)` regardless of track length. This is what
   `align`'s final `index1`/`index2`/`hop_seconds` (now 10ms, not 50ms) and
   every downstream `TimeMap` are built from -- alignment got *more*
   precise as a side effect of the banding, not just cheaper.

`ALIGN_MAX_NORMALIZED_DISTANCE` is recalibrated (40.0 -> 70.0) against this
new cost function's own scale, empirically, against this test suite's
synthetic fixtures: legitimate-but-different takes measured 2-43,
genuinely unrelated signals measured 120-1050 -- 70 sits with margin on
both sides of that gap.

## Consequences

- `align`'s memory footprint is bounded by the band, not by either
  sequence's length, for any input this repository ever hands it.
- Alignment output moved from 50ms to 10ms resolution, tightening every
  stage that resamples through `TimeMap` (rhythm, dynamics, timbre, breath)
  for free.
- A real bug was caught building this: the coarse and fine hops' nominal
  frame counts don't cover exactly the same duration (`librosa`'s
  `1 + n // hop_length` rounds differently per hop), and `np.interp`
  clamping instead of extrapolating past the coarse path's own range
  collapsed the whole tail of a long track onto one center value, outside
  the refine band. Fixed by spreading that end-of-track gap linearly across
  the projection (`dsp/dtw.py::refine_center`); a dedicated regression test
  reproduces the exact frame counts that exposed it on a real ~207s song.
- `dtw-python` is dropped as a dependency entirely.
- The DTW cost scale is now this repository's own; `ALIGN_MAX_NORMALIZED_DISTANCE`
  is a fresh empirical starting point like every other scoring threshold
  (spec 19), not carried over from `dtw-python`'s `symmetric2`.

## Alternatives considered

- **Keep `dtw-python`, pass a smaller window** -- rejected: the window only
  changes which cells are *masked*, not whether the underlying matrix is
  allocated; memory stays `O(n * m)` regardless of window size.
- **A different existing banded-DTW package** -- none evaluated ship a
  `numba`/vectorized banded kernel with the exact contract this pipeline
  needs (monotonic path from `(0,0)`, an upfront cell-count guard, a
  variable-center band for level 2); writing the ~150-line kernel directly
  was less risk than adapting a general-purpose library's internals to fit.
- **Single-level DTW at 10ms directly, banded** -- rejected on cost: a
  10ms-hop band wide enough to bound a 6-minute recording's plausible tempo
  drift (spec 6.7's original motivating number, ~3·10^8 cells unbanded)
  still costs meaningfully more than a coarse pass plus a narrow refine
  pass at the same final resolution.
