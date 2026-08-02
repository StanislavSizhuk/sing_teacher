# ADR-0034: separate the user's `mixed`-mode recording with Demucs too

- Status: Accepted
- Date: 2026-08-02
- Supersedes: ADR-0025 (melody extraction as A4's implementation)
- Amends: ADR-0003 (scopes its reasoning to `clean` only)

## Context

A real, repeated `ALIGNMENT_FAILED` on real `mixed`-mode recordings traced
to the actual root cause, not a tuning problem: `extract_melody`
(`dsp/melody.py`, ADR-0025's harmonic-salience heuristic -- the only pitch
source the user's own recording ever got in `mixed`, since ADR-0003 kept
Demucs off the user's recording entirely) reports 87.5% of a recording as
"voiced", while the reference's own real Demucs-separated vocal stem for
the same song is genuinely only ~60% voiced -- confirmed directly: ~63s of
real instrumental-only sections across 6 breaks, RMS -45 to -60 dB
relative to peak during those sections vs -4.6 dB during real vocal
content. `extract_melody`'s voicing-ratio threshold does not reliably
reject a confident-sounding instrumental line as "not a vocal", so the
recording's own pitch curve does not structurally match the reference's
real silence pattern, and DTW's warping path drifts wildly trying to
reconcile them -- confirmed directly: even at a generous ±10s band, the
path saturates at the band edges with high variance, regardless of
ADR-0033's pitch-contour embedding (tested and ruled out).

This is not a symptom ADR-0033 could have fixed: aligning on pitch contour
instead of MFCC removes sensitivity to *timbre*, but the two curves being
compared here already have different *structure* -- one from a neural
separator, one from a DSP salience heuristic -- before contour comparison
even starts. No warping-band or distance-metric tuning changes that a
loud instrumental passage confidently "sounds like a voice" to one
extractor and not the other.

ADR-0003's own alternatives-considered section rejected running Demucs on
both tracks, but that reasoning was explicitly scoped to the a cappella
assumption: "doubles Demucs cost for no accuracy gain under the a cappella
assumption". `mixed` recordings genuinely have accompaniment to remove;
the accuracy gain here is real and now measured, not hypothetical.

## Decision

Run real Demucs separation on the user's own `mixed`-mode recording, the
same `VocalSeparator` (`pipeline/registry.py`) the reference already uses.
A new stage, `SeparateRecordingStage` (`modes={"mixed"}`), runs right after
`preprocess`. Its output stem replaces the raw recording for every stage
that reads user audio -- `FeaturesStage` and `AlignStage`, the only two
consumers of `preprocess`'s `recording_path` -- through one resolver,
`pipeline/voice_source.py::voice_audio_path`, so the shared feature cache
(`rms_fine`, `mfcc`) and align's own pitch extraction always agree on which
audio they are looking at. Everything downstream of the shared cache
(`rhythm`, `dynamics`, `vibrato`, `align`) needs no change at all.

`MelodyPitchStage` is deleted outright rather than kept as a fallback:
after ADR-0033 moved extraction into `align`, its `run()` body was already
byte-identical to `PitchStage`'s (both only read the curve `align` already
produced and score it). `PitchStage` now simply runs in both modes.
`dsp/melody.py` and its dedicated constants are deleted with it -- nothing
else in the pipeline depends on `extract_melody`.

`recording_condition` (spec 6.16, mode/content reconciliation) keeps
reading the **raw** pre-separation recording, not the new stem: its whole
purpose is comparing what the user recorded against what they declared,
and a stem that has already had accompaniment removed would trivially
"detect" every `mixed` recording as clean.

Reused, not rebuilt: `VocalSeparator` protocol and
`ModelRegistry.vocal_separator()` (already lazy-loaded, already the
reference's own separator instance and cache), `measure_and_normalize`
loudness handling, the `NO_VOICE_DETECTED` error path (no new error type --
a stem that comes back essentially silent already fails the existing
`voiced_fraction < MIN_VOICED_FRACTION` gate in `align`).

## Consequences

**Wall-time**: mixed-mode warm path stops being cheap. Measured Demucs
cost on the reference (`docs/PERFORMANCE.md`): 90.7s for a 225s track on a
12 vCPU dev machine, against `SEPARATE_REFERENCE_TIMEOUT_SECONDS = 600`.
The prior mixed-mode estimate (~20.1s total, 13-16% of a 150s budget) is no
longer valid -- NFR-01c is raised to 300s (from 150s) and the new stage's
own timeout matches the reference's, 600s.

Verified directly (`docs/PERFORMANCE.md`'s "M3.1 measurement"), not left as
a guess: a real 165s recording (the same track used as its own reference --
the exact case that used to fail) ran through `separate_recording` in
113.1s on the same 12 vCPU dev machine, and the full measured warm path
(A1+A1b+A6+A7) came to ~128.7s against the new 300s ceiling. `align`
succeeded with `normalized_distance = 0.0238`, well under the 0.45
ceiling, where it previously raised `ALIGNMENT_FAILED` on this same
song/reference pair. Still open: 4 vCPU is unmeasured, and this run
re-used a studio-quality source as both sides rather than a genuinely
noisy self-recording -- both timeout/budget numbers keep their margin
(P2's own 420s budget line, itself well above its own 90.7s measurement,
is the honest range to read A1b's worst case against) until those are
measured too.

**Queue reclaim**: `PENDING_CLAIM_MIN_IDLE` (15 min, `analyses:run`) was
sized specifically on "no single warm-path stage runs as long as Demucs" --
that assumption is now false for `mixed`. Raised to 20 minutes, matching
`SONGS_PREP_PENDING_CLAIM_MIN_IDLE`; the two-tier reclaim threshold this
project had since ADR-0024 no longer has a reason to differ.

**Scoring stays comparable**: `mixed_v1`'s aspect set is unchanged --
`timbre`/`breath` stay `null` (ADR-0027). Demucs separation artifacts
(musical noise, formant smearing) would make those two aspects actively
misleading if scored, worse than the honest "not measurable" they already
report. `scoring_version` is not bumped -- weights are unchanged -- but
`mixed`-mode scores from before and after this change are not directly
comparable, since the pitch curve being scored now comes from a
differently-processed signal; recorded here rather than silently.

**`clean` mode is entirely unaffected** -- it never had accompaniment to
remove, and ADR-0003's original reasoning stays valid for it unchanged.

## Alternatives considered

- **Tune `MELODY_VOICING_SALIENCE_RATIO` instead** -- rejected: still a
  heuristic with no guaranteed reliability against a confidently
  in-key accompaniment line, the exact failure ADR-0025 already named as
  the DSP approach's known limitation. Would not fix the structural
  mismatch (different extractor characteristics between reference and
  recording), only move the threshold where it breaks.
- **Keep `extract_melody` as a fast-path fallback, try Demucs only on
  alignment failure** -- rejected for now: doubles the code paths scoring
  has to reason about (two different pitch-curve characters feeding the
  same downstream comparison) for a latency optimization that has not been
  shown to matter yet. Worth revisiting if mixed-mode latency proves a
  real problem in practice; the interface (`VocalSeparator` in, pitch curve
  out) does not block adding this later.
- **Widen the DTW band further / raise `ALIGN_PITCH_MAX_NORMALIZED_DISTANCE`**
  -- rejected: already measured to saturate at the band edges with high
  variance even at a generous ±10s band; the failure is structural
  (mismatched silence pattern), not a threshold being slightly too strict.
