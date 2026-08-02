# ADR-0032: search for the reference's start offset before giving up alignment

- Status: Accepted
- Date: 2026-08-02

## Context

ADR-0030 made `align` tolerant of a recording that is simply shorter or
longer than the reference, by cropping whichever side is longer down to
the shorter side's exact length before either DTW pass. That fix shares
one assumption with the DTW itself (`dsp/dtw.py`'s own doc comment, spec
6.7): **both signals start together** -- `banded_dtw`'s coarse pass bands
around the literal diagonal (`full_center[i] = i`), so frame 0 of the
recording is always forced to match frame 0 of the reference.

That assumption breaks for an ordinary, common recording: a reference
that opens with an instrumental intro (spec 2.3 example: `програш`)
before the vocal begins, sung over by a user who -- reasonably --
started recording once *they* started singing, not at the reference's
own bar one. The recording's frame 0 actually corresponds to some later
frame `k` in the reference, not frame 0. Confirmed against a real
production failure (worker log, this session):

```
DTW normalized distance 217.4 exceeds the 70.0 ceiling -- recording and
reference diverge too far in tempo/content to align reliably
```

-- `preprocess`/`features` both completed normally (both files decode
and load fine), so this is not a bad upload; it is `align` faithfully
doing what it is built to do: reject an alignment that, forced to start
at (0, 0), never finds a low-cost path, exactly spec 6.8's risk table
wants for a *genuine* mismatch. The difference is that this recording
and reference are almost certainly the same song -- just not lined up at
the start -- and today there is no way to tell those two cases apart,
so both fail identically.

Whether this fails outright or merely scores badly depends on how long
the intro is relative to `ALIGN_WINDOW_SECONDS = 10.0`: an offset under
that band might still let the coarse pass reach the far corner (just
via an unnaturally warped, high-cost path -- the `> ALIGN_MAX_
NORMALIZED_DISTANCE` failure mode), while a longer intro makes the
target unreachable within the band at all (`banded_dtw`'s own
"unreachable" `AlignmentFailed`, raised before any cost is even
computed). Both are symptoms of the same root cause and should be
handled the same way.

## Decision

When the existing (ADR-0030) crop-to-overlap-then-align attempt fails --
either failure mode above -- `align` gets one fallback attempt before
raising `AlignmentFailed` for real: search for the reference frame the
recording's own start actually corresponds to, then re-run the
*existing, unchanged* two-level pipeline anchored there.

Two phases, deliberately not one big new DTW variant:

1. **Cheap candidate scan.** Over candidate reference start frames `k`
   in `[0, ALIGN_MAX_START_OFFSET_SECONDS / FEATURES_HOP_SECONDS]` (a
   new named constant, coarse-hop frames), score each candidate with a
   direct (unwarped) frame-by-frame distance between the user's coarse
   MFCC and `reference_mfcc[k : k + n]` -- no DTW, no banding, just
   `O(n)` per candidate, so the whole scan is `O(n * search_range)`, a
   small bounded cost independent of the reference's total length.
   Keeps the few lowest-scoring candidates.
2. **Verify with the real pipeline.** For each kept candidate `k`, crop
   the reference to start at `k` (composing with `_crop_to_overlap` for
   any remaining length difference, exactly as before) and run the
   *same* two-level `banded_dtw` pipeline `align` already runs, against
   the *same* `ALIGN_MAX_NORMALIZED_DISTANCE` ceiling. The first
   candidate that passes wins; none passing means this was never just
   an offset problem, and `align` raises `AlignmentFailed` exactly as it
   does today.

Phase 2 reusing the existing pipeline unchanged is the point: the
tempo-tolerant band, the refine pass, the one acceptance ceiling, the
`WarpingPath` shape every downstream stage already expects -- none of it
needs to know a start offset was ever searched for. `AlignStage`'s
result gains `reference_start_offset_seconds` (0.0 when untouched, for
observability the same way `coarse_normalized_distance` already is) and,
when non-zero, a new warning code (`REFERENCE_START_OFFSET_DETECTED`)
threaded through `confidence.py`/`AggregateStage` exactly the way
`length_mismatch` already is (confidence step-down, not a failure) --
the user sang the right song, just not lined up with the reference's own
bar one, which is worth surfacing, not hiding.

## Consequences

Gets easier: the exact case reported this session -- reference with an
intro, recording starting at the voice -- now scores instead of failing
outright, with an honest warning that the reference's start was
adjusted, same honesty pattern ADR-0030 already established.

Gets harder: adds a second, slower attempt on the failure path only (the
common, already-correctly-started case pays nothing extra); a plausible
song intro can run longer than whatever `ALIGN_MAX_START_OFFSET_SECONDS`
ends up set to, in which case this still fails exactly as today -- an
explicit, tunable bound rather than an unbounded search, and, like
`ALIGN_WINDOW_SECONDS`/`ALIGN_MAX_NORMALIZED_DISTANCE` (ADR-0017), a
starting empirical guess rather than a calibrated one. Does not handle
the recording itself containing extra unsung lead-in before the singing
starts (throat-clearing, a count-in) -- phase 2 still requires the
recording to be matched in full from its own frame 0, so that case is a
distinct, not-yet-solved problem, out of scope here. Two DTW-shaped
things to reason about (the existing diagonal-banded pipeline, plus the
new unwarped candidate scorer) instead of one, though the second is
intentionally the simpler of the two.

## Alternatives considered

- **True subsequence DTW** (open-begin *and* open-end boundary
  conditions on one unbanded pass over the full coarse representation,
  a standard technique for "find where a clip fits inside a longer
  track") -- rejected: unbanded means `O(n * m)` memory over the *whole*
  reference, exactly what NFR-16 and `dsp/dtw.py`'s own banded design
  exist to avoid, and for two near-the-6-minute-cap tracks that's
  comparable to `DTW_MAX_CELLS`'s existing ceiling on a *banded* pass.
  Worth revisiting only if the cheap-scan-then-verify approach proves
  too approximate in practice.
- **Widen `ALIGN_WINDOW_SECONDS` itself** -- rejected outright: that
  constant's own doc comment (`dsp/dtw.py`) already explains why it
  stays a fixed, diagonal-centered band rather than a length-ratio-scaled
  one -- widening it would let a genuine content mismatch reach the far
  corner too, defeating the rejection spec 6.8's risk table depends on.
  This ADR's search is scoped (a bounded, separate search over
  *candidate offsets*, verified against the unchanged existing ceiling)
  specifically to avoid that failure mode.
- **Ask the user to trim their own recording** -- the honest fallback
  when the search still fails (see Consequences), but not a fix; already
  possible today with no code change, and not what was asked for.
