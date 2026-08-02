# ADR-0030: `align` crops a length-mismatched recording to the overlap instead of failing outright

- Status: Accepted
- Date: 2026-08-02

## Context

`AlignStage`'s coarse DTW pass (`worker/src/vocalcoach/dsp/dtw.py`,
ADR-0004/ADR-0017) runs banded around the literal diagonal, radius
`ALIGN_WINDOW_SECONDS = 10.0`, deliberately *not* scaled to the two
sequences' length ratio -- the existing rationale (still correct, still
kept) is that scaling the band would make two wildly different-length
recordings always reach the far corner, defeating the "diverges too far
to align" rejection spec 6.8's risk table wants for a genuine content
mismatch (wrong song, silence, noise).

That same mechanism, however, was also the only thing standing between a
merely-shorter-or-longer recording and a hard `ALIGNMENT_FAILED`. A take
cut short, or one that simply kept going after the reference track ended,
has nothing wrong with its *content* -- the portion that does overlap
could align perfectly -- but the moment the length difference alone
exceeded the 10s band, the whole analysis failed with no result at all.
Surfaced directly by a user testing FR-20 (record in browser): a short
test recording against a full-length reference failed with
`ALIGNMENT_FAILED`, which rendered in the UI as a bare error code
indistinguishable from an actual service fault.

## Decision

`AlignStage.run` (`worker/src/vocalcoach/pipeline/stages/align.py`) now
calls `_crop_to_overlap` before either DTW pass: when the coarse-hop frame
counts differ by more than `coarse_band`, it crops whichever side is
longer down to **exactly** the shorter side's frame count (not
shorter-plus-band -- see the function's own docstring: both `banded_dtw`
calls it feeds always force their sequence's last frame to match the
other's last frame, so cropping with the extra band's worth of slack
would force the shorter side to be unnaturally stretched across it,
producing an incoherent path that then routinely violates level 2's much
narrower refine band). The fine-hop pair is cropped to the same time
extent, in seconds, right after computing it.

The stage no longer raises for a pure length mismatch. It records
`length_mismatch: bool` in its own `StageResult.data`. `AggregateStage`
threads that into `ConfidenceSignals` (`scoring/confidence.py`), which
steps confidence down one level and emits a new warning code,
`LENGTH_MISMATCH_PARTIAL_ANALYSIS` -- the same shape every other
confidence signal already uses (`ACCOMPANIMENT_IN_CLEAN_MODE`,
`WEAK_ALIGNMENT`, `KEY_SHIFT_OUT_OF_RANGE`), not a new failure mode. A
genuine content mismatch at comparable lengths (wrong song, silence,
noise, or content that still doesn't align once cropped to the overlap)
still raises `AlignmentFailed` exactly as before -- `dtw.py` itself is
unchanged.

Separately, `QueueStatus.tsx` no longer renders any terminal analysis
error code as a raw `Error: CODE_NAME`. All seven codes
(`worker/src/vocalcoach/errors.py`) now map to a plain-language sentence
(`web/src/i18n/translations/{en,uk}.ts`'s new `analysisError` section),
falling back to naming the code (never hiding it) for one not yet
mapped -- the same "code is stable, detail is for humans" split
`ErrorAlert.tsx`'s `FRIENDLY_MESSAGES` already established for
request-level errors (spec 8.1).

## Consequences

Gets easier: a recording that's simply shorter or longer than the
reference -- common, not itself evidence of a bad take -- now produces a
real, honestly-caveated result instead of no result at all. The warning
and confidence step-down make clear that only part of the song was
scored, so the number itself is never presented as more complete than it
is.

Gets harder: the crop assumes both signals start together (the same
assumption the diagonal band already made) -- a recording that starts
partway through the song (skips the intro, starts on the second verse)
is not handled by this change and will still either misalign or fail;
that is a genuine subsequence-alignment problem (find *where* in the
reference the recording best fits, not just how much of it to compare),
out of scope here. `_crop_to_overlap` only ever trims the *end* of the
longer side.

## Alternatives considered

- Scale the coarse band to the length ratio instead of cropping --
  rejected: this is exactly the change the existing code comment already
  warned against (spec 6.8's risk table), since it would let a *content*
  mismatch at very different lengths reach the far corner too, defeating
  the rejection that risk table depends on.
- Crop to shorter-plus-band instead of exactly shorter -- tried first,
  reverted: produces an incoherent warping path (see Decision) that
  routinely fails level 2's narrower refine band, confirmed against a
  synthetic fixture before landing on the exact-length crop.
- True subsequence DTW (let the recording match any contiguous window of
  the reference, not just a same-start prefix) -- not implemented: a
  materially larger change to the DP formulation (open initial boundary,
  minimum-cost search over the last row instead of a fixed corner), and
  no concrete case yet needs it (every observed length mismatch so far is
  a take cut short or run long, not one that starts mid-song). Revisit if
  that need becomes real.
