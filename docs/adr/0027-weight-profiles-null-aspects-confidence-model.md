# ADR-0027: Weight profiles, null aspects, and the confidence model

- Status: Accepted
- Date: 2026-07-31
- 2026-08-02: ADR-0034 deletes `MelodyPitchStage` -- after ADR-0033 moved F0
  extraction into `align`, its `run()` was already byte-identical to
  `PitchStage`'s, so `PitchStage` now simply runs in both modes
  (`modes` widened to `ALL_MODES`) and still writes result name `"pitch"`.
  The "shared stage name, disjoint `modes`" mechanism this ADR designed
  (Decision, question 1) is exactly what made that deletion a non-event for
  `VibratoStage`, `AggregateStage`, and score persistence -- none of them
  needed to change. `MODE_ASPECTS` and both weight profiles are unaffected;
  `mixed_v1` still scores four aspects, `timbre`/`breath` still `null`.

## Context

Spec 6.14/6.15 (M3) require: two named weight profiles (`clean_v1` scoring
all six aspects, `mixed_v1` scoring four), an aspect a mode never measures
reported as `null` with a machine-readable reason rather than `0` (FR-41),
and a `high`/`medium`/`low` confidence level -- overall and per-aspect --
driven by named signals (mode, accompaniment-in-clean, low voiced ratio,
weak alignment, out-of-range key shift, low melody-extraction confidence).
Spec 12.3 additionally requires this logic live in `scoring/`, not inside a
stage, and requires a stage declare `modes` rather than branch on
`if mode ==` internally.

Two implementation questions followed directly:

1. **How does `mixed` get its pitch/vibrato source (melody extraction, A4)
   instead of direct pitch detection (A5) without `AggregateStage`,
   `VibratoStage`, or the job handler's score persistence needing to know
   which one ran?** All three currently read a stage literally named
   `"pitch"` (`context.result("pitch")`, `stages_json["pitch"]`,
   `analyses.pitch_score`).
2. **How does aggregation avoid a `KeyError` on `context.result("breath")`/
   `context.result("timbre")` in `mixed`, where those stages never run at
   all (not merely fail)?**

## Decision

**Shared stage name, disjoint `modes` (question 1):** `MelodyPitchStage`
(`pipeline/stages/melody.py`, A4, `modes={"mixed"}`) produces the exact
same `StageResult` shape as `PitchStage` (A5, `modes={"clean"}`) and writes
it under the same name, `"pitch"`. `PipelineRunner`'s mode filtering (spec
12.3) guarantees at most one of the two ever runs in a given analysis, so
there is never a name collision within one run's `context.completed` --
only across the two mutually-exclusive stage classes that could produce it.
`VibratoStage`, `AggregateStage`, and the job handler's `_persist_scores`
needed zero changes for this: they already only ever look up `"pitch"` by
name, never by which class produced it.

**`scoring/weights.py` owns `MODE_ASPECTS`** (which aspects a mode scores
at all) **and the weighted-sum formula**; `config.py` still owns parsing
`SCORING_WEIGHTS_CLEAN`/`SCORING_WEIGHTS_MIXED` from the environment (spec
12.1: config loading stays config's job), validated against
`MODE_ASPECTS[mode]` so a clean-shaped profile cannot be substituted for
mixed's or vice versa. `unavailable_aspects_for(mode)` answers question 2
directly: `AggregateStage` iterates `MODE_ASPECTS[context.mode]`, never the
full six, so it never looks up a stage result that mode was never going to
produce; the aspects it excludes are exactly `unavailable_aspects_for`'s
keys, each mapped to `NOT_MEASURABLE_WITH_ACCOMPANIMENT` (FR-41).

**`scoring/confidence.py`** takes a small `ConfidenceSignals` dataclass
(`mode`, `accompaniment_in_clean`, `voiced_ratio`, `alignment_cost`,
`key_shift_out_of_range` -- each gathered off an earlier stage's result by
`AggregateStage`) and returns the overall level, a per-aspect map, and the
warning codes that fired. Pure function, no `PipelineContext`/`StageResult`
knowledge, so it is unit-tested directly on inputs rather than through a
full stage run.

**A8 (`KeyNormalizationStage`) runs in both modes unconditionally** and
decides internally, from `context.mode`/`context.allow_transposition` and
the measured shift's own size/stability, whether to actually apply a
correction (spec 6.8's own conditions). This is not the `if mode ==`
stage-selection branching spec 12.3 forbids -- that concern is which stages
run at all (`PipelineStage.modes`), not what a stage that always runs
chooses to compute. Because `PitchStage`/`MelodyPitchStage` fully own pitch
detection and reference comparison already, A8 does not re-run either: it
reads the "pitch" stage's already-computed `piano_roll.deviation_cents`,
takes the median (the shift) and IQR (the stability check) directly from
that, and -- only if the shift is applied -- recomputes the pitch aspect's
score with the shift subtracted out via the same
`dsp/pitch_scoring.score_from_mean_abs_cents` the pitch stage itself used.
`AggregateStage` substitutes this adjusted score for the aspect whenever
A8 applied a shift.

## Consequences

- Adding a third pitch source in the future (say, a future ONNX melody
  model per ADR-0025's "alternatives considered") means adding a third
  class named `"pitch"` with a disjoint `modes`, not touching
  `AggregateStage`, `VibratoStage`, or persistence at all.
- `scoring/`'s pure functions (`weighted_overall_score`,
  `unavailable_aspects_for`, `compute_confidence`) are unit-testable
  without building a full `AnalysisContext` or running any stage.
- A8 depends on stage `"pitch"` having already run and produced a
  `piano_roll` -- ordering that dependency correctly in `worker.build_stages`
  is the one place this decision has a real footgun: putting A8 before
  `"pitch"` in the stage list would break silently (a `KeyError` at
  runtime, not an import-time error), since nothing in the type system
  encodes stage-ordering dependencies.
- Vibrato needed no shift-awareness: a constant semitone offset does not
  change a pitch curve's own oscillation rate/depth, only its absolute
  level, so A8's correction is pitch-only by construction, not an
  oversight.

## Alternatives considered

- **A single `PitchStage` with an `if mode == "mixed": ... else: ...`
  branch inside `run()`** -- rejected outright: spec 12.3 forbids exactly
  this, and it would also force the stage onto whichever `modes` superset
  covers both algorithms, defeating the runner's own mode filtering for no
  benefit over two classes.
- **Rename the mixed-mode stage to `"melody"` and teach every consumer
  (`AggregateStage`, `VibratoStage`, persistence) to look up either name**
  -- rejected: reintroduces an `if mode ==`-shaped branch at every call
  site, the opposite of what shared naming buys.
- **Weight-profile combination function inside `AggregateStage` itself**
  -- rejected per spec 12.3's explicit instruction that this lives in
  `scoring/`; also would not be unit-testable without a full stage run.
- **A8 re-running full pitch detection and reference alignment itself,
  independently of stage `"pitch"`** -- rejected as a DRY violation (spec
  12.1): the comparison math already exists once, in
  `dsp/pitch_scoring.py`, specifically so a second pitch-scoring caller
  would not have to re-derive it.
