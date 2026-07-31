# ADR-0023: Energy-based VAD gate for M1; Silero VAD deferred to M2

- Status: Accepted
- Date: 2026-07-30

## Context

Spec 6.5/6.6 (stage A2) name Silero VAD (ONNX runtime) as the pipeline's
voice-activity gate: a cheap voiced/silent mask so expensive per-frame
stages skip stretches of silence. Measured on a real ~207s song
(`docs/PERFORMANCE.md` "before"), pitch detection is the single most
expensive warm-path stage by far -- 38.8s of 64.4s total (60%) -- running
CREPE over the *entire* recording, including the 30-50% of it spec 6.6
itself says is typically silence between phrases.

M1 (spec 18) is scoped to "the existing single pipeline... without changing
queue architecture" -- it does not introduce the cold/warm path split (M2)
or the mode/melody-extraction machinery (M3) that spec 6.5's Silero VAD
was originally specified alongside. Adding a real ONNX runtime dependency,
a model-weights entry, and a checksum-verified download step (spec 11.3)
for VAD alone, ahead of the milestone that otherwise needs an ONNX runtime
for other reasons (melody extraction, M3), is more infrastructure than
this milestone's actual goal -- gating the one stage profiling identified
as expensive -- requires.

## Decision

Implement the VAD gate now with a plain energy-relative-to-peak threshold
(`worker/src/vocalcoach/dsp/vad.py`), reusing the exact technique
`breath.py`'s pause detection already uses (`BREATH_SILENCE_RELATIVE_DB`),
plus a minimum-silent-run length (`VAD_MIN_SILENT_RUN_SECONDS`) so a
handful of quiet frames isn't worth the gating overhead. `PitchStage` runs
the pitch detector only over the resulting voiced spans (each independently,
so a span's own length is the only frame count its output needs to line up
with), filling every other frame with `None` directly -- no detector call
over silence at all, rather than running it and discarding an "unvoiced"
result.

Silero VAD (ONNX) remains the target implementation spec 6.5/6.6 name, and
lands with M2's cold/warm split, when the ONNX runtime/model-weights
infrastructure it needs is already being introduced for melody extraction
(M3) and is no longer new infrastructure for VAD alone to justify.

## Consequences

- Pitch detection is gated now, on real measured savings, with zero new
  dependencies -- `dsp/vad.py` uses only `numpy`, already a dependency.
- The VAD mask this milestone produces is a byproduct of `PitchStage`'s own
  call to it; it is not yet a first-class `PipelineContext` artifact other
  stages (`align`'s level-2 refinement, in particular) consume -- level 2's
  refinement covers the whole track, not just voiced frames, a documented
  simplification in ADR-0017, not an oversight.
- Swapping to Silero VAD later replaces `dsp/vad.py`'s `voiced_mask`
  implementation behind the same signature (`rms -> bool mask`); `PitchStage`'s
  gating logic (`_detect_gated`) does not need to change, only what
  produces the mask.
- Accuracy trade-off: an energy threshold is fooled by loud non-vocal
  sounds (a cough, a loud consonant burst) and can miss very quiet singing
  -- acceptable here because the gate only decides *whether to run the
  detector*, never the analysis outcome itself; a wrongly-gated frame comes
  back `None` (unvoiced), the same outcome a real VAD's false negative
  would produce, and `MIN_VOICED_FRACTION`'s `NO_VOICE_DETECTED` check is
  unaffected either way.

## Alternatives considered

- **Silero VAD now, ahead of M2/M3** -- rejected per the infrastructure-cost
  reasoning above: a new ONNX dependency and a checksum-verified model
  download (spec 11.3) for one stage's gate, before the milestone that
  makes that infrastructure cost shared across multiple features.
- **No VAD gate in M1, defer entirely to M2** -- rejected: M1's own
  acceptance list (spec 18) explicitly names the VAD gate (spec 6.5, A2),
  and the profiling data makes the win too large to leave on the table for
  two more milestones.
- **Gate on the pitch detector's own voiced/unvoiced output, computed
  after the fact** -- rejected: that requires running the expensive
  detector first to learn where it was expensive, which gates nothing.
