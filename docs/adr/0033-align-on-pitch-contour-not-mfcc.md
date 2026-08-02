# ADR-0033: align on pitch contour, not MFCC

- Status: Accepted
- Date: 2026-08-02

## Context

`align` (A3, spec 6.7) finds the time correspondence between a recording
and its reference by running banded DTW over **MFCC**
(`dsp/features.py`'s shared cache) -- the exact same representation the
`timbre` aspect score (A9) uses to judge how similar two voices *sound*.
MFCC captures timbre/spectral envelope: formants, voice quality, mic and
room character. That makes alignment sensitive to *who* is singing, not
just *what* is sung -- two people singing the identical melody of the
identical song, in different voices, at different registers, through
different microphones, can produce MFCC distant enough that DTW never
finds an acceptable path.

A user hit this directly this session, on a real recording, insisting it
was the same song as its reference. The worker logs bore this out: every
attempt failed with `banded_dtw`'s structural "no warping path within the
configured band" rejection, not the cost-ceiling one -- consistent with a
real, voiced recording whose *timbre* just doesn't resemble the
reference's closely enough, not evidence of a wrong song. ADR-0030 (crop
to overlap) and ADR-0032 (search for the reference's start offset) both
already patched around length/offset symptoms of alignment fragility
without touching the underlying signal; this is the first fix to the
signal itself.

## Decision

Align on **pitch contour** (melody shape) instead of MFCC. Melody is
largely invariant to who is singing; it is also literally what "the same
song" means, more directly than timbre ever was.

**The two curves already exist with no new dependency on align.**
`PitchStage` (`clean`) and `MelodyPitchStage` (`mixed`) already extract
the user's raw F0 curve (`detect_gated` / `extract_melody`) with zero
dependency on align's own output -- they only consult it afterward, to
map the curve onto the reference's timeline for scoring. The reference's
F0 curve is not per-analysis work at all: it is cold-path output, already
cached on `AnalysisContext.reference_pitch` before align ever runs. This
is a re-plumbing of an existing computation's *order*, not new DSP.

**Distance metric that reuses the existing kernel unchanged.**
`_banded_dtw_kernel` computes plain Euclidean distance over `(n, d)`
feature vectors -- it has no idea what the vectors mean. Rather than
teach it a custom musical distance, each pitch value embeds as a 2-D
point on the unit circle, one full turn per octave:
`theta = 2*pi * frac(log2(hz / PITCH_FMIN_HZ))`, `embedding = (cos theta,
sin theta)`. Two frames a whole number of octaves apart land on (near)
the same point -- octave errors (a known pitch-tracker failure mode) and
ordinary octave differences between voices stop looking like a large
distance, without losing the ability to reject a genuinely different
melody. An unvoiced/silent frame embeds to `(0, 0)`, the circle's center:
distance to any voiced point is a constant `1.0` (a real, moderate
mismatch signal), distance between two unvoiced frames is exactly `0.0`
(both silent at the same moment is a real match) -- both fall out of the
embedding for free, no special-cased branch in the kernel. The whole
distance range is a small, fixed `[0, 2]`, unlike MFCC's open-ended
scale.

**`align.py` extracts pitch itself.** `AlignStage` gains the same
mode-aware extraction `PitchStage`/`MelodyPitchStage` already do
(`detect_gated` for `clean`, `extract_melody` for `mixed`), and reads
`context.reference_pitch.hz` directly for the reference side.
`NoVoiceDetected`/`MelodyExtractionFailed` (the existing voiced-fraction
floor check) moves here too: if there is not enough voice to embed
reliably, alignment on pitch is exactly as unreliable as scoring on it
would have been, so failing before a DTW pass on noise is strictly
better than today's order. Coarse pass: pitch is only ever extracted at
`PITCH_HOP_SECONDS` (10ms) -- no separate coarse extraction the way MFCC
had one -- so it strides every `round(FEATURES_HOP_SECONDS /
PITCH_HOP_SECONDS)` frames instead. Fine pass: the full 10ms curve
directly, removing align's separate fine-hop MFCC computation entirely.
`AlignStage`'s result gains `user_pitch_curve` (same shape
`PitchStage`/`MelodyPitchStage` already produced), so `PitchStage`/
`MelodyPitchStage` shrink to scoring only -- they read the curve back
instead of re-extracting it, a real duplication removed, not added.

ADR-0030's `_crop_to_overlap` and ADR-0032's `_attempt_align`/
`_find_reference_start_offset` operate generically on `(n, d)` arrays via
`banded_dtw` -- they carry over unchanged, just fed `d=2` embeddings
instead of `d=13` MFCC. Neither of the last two ADRs' work is
superseded by this one; it composes with both.

`ALIGN_MAX_NORMALIZED_DISTANCE = 70.0` is explicitly calibrated for
13-dim MFCC Euclidean distance (its own comment says so) and cannot be
reused for a `[0, 2]`-bounded embedding. A new constant,
`ALIGN_PITCH_MAX_NORMALIZED_DISTANCE`, is calibrated the same empirical
way: synthetic fixtures for legitimate variation (same melody, shifted
register) vs genuinely different melody, picked so the former passes and
the latter still doesn't.

## Consequences

Gets easier: "same melody, different voice/register" now aligns and
scores instead of failing outright -- the case that prompted this ADR.
`pitch`/`melody` stages get simpler (scoring only, one fewer redundant
extraction per analysis).

Gets harder: `AlignStage`'s constructor now takes a `PitchDetector` (every
construction site -- `worker.py::build_stages`, every test building an
`AlignStage` directly -- needs updating); a recording with too little
voice now fails at align (`NoVoiceDetected`/`MelodyExtractionFailed`)
instead of at the (later) pitch stage -- same error codes, earlier in the
pipeline, a strictly more honest ordering, but any test that relied on a
silent/near-silent fixture reaching `AlignmentFailed` specifically needs
its fixture changed to two *voiced* but differently-melodied signals.
`dsp/features.py`'s MFCC cache stays exactly as it is -- `timbre` (A9)
still needs it -- so this is not "MFCC computation removed," only
"align stops being one of its two remaining readers."

## Alternatives considered

- **MFCC with a looser ceiling / wider band** -- rejected: doesn't
  address the root sensitivity (timbre, not melody), and the existing
  band is deliberately narrow for a real reason (spec 6.8's risk table,
  ADR-0032's own alternatives section already covers why widening it
  defeats the rejection).
- **Chroma feature vectors (12-bin) instead of a 2-D angle embedding** --
  rejected as unnecessarily heavier: chroma needs its own spectral
  analysis pass over the raw audio, duplicating work the pitch
  curve/embedding already does in two float32 numbers per frame, for the
  same octave-invariance property this embedding already gives directly
  from the F0 value already being extracted anyway.
- **Fall back to pitch only when MFCC fails (hybrid, mirroring ADR-0032's
  retry shape)** -- rejected in favor of a full switch: MFCC's
  timbre-sensitivity is not an edge case needing a fallback, it is the
  root cause for the common case this ADR targets, and every recording
  benefits from the fix, not just ones that already failed once.
