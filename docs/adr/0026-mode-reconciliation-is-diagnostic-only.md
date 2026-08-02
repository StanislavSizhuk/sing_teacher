# ADR-0026: FR-29/FR-30 mode reconciliation is diagnostic, not stage-selecting

- Status: Accepted
- Date: 2026-07-31
- 2026-08-02: ADR-0034 replaces `mixed`'s melody extraction (A4) with Demucs
  separation of the recording, read by A3 (`recording_condition`, this ADR)
  from the **pre-separation** raw recording specifically so this stage's
  own accompaniment-detection logic still has something to detect --
  reconciliation's diagnostic-only status and every decision below are
  otherwise unaffected. The melody-extraction-specific cost note in
  Consequences (below) is historical: `dsp/melody.py` no longer exists, a
  `mixed`-declared, actually-a-cappella recording now pays an unnecessary
  Demucs pass instead, not an unnecessary melody-extraction one.

## Context

Spec 6.16 (M3) has stage A3 classify whether the user's recording actually
contains accompaniment and reconcile that against the mode they declared:

| Declared | Detected | Action |
|---|---|---|
| `clean` | accompaniment | analysis proceeds, `ACCOMPANIMENT_IN_CLEAN_MODE`, confidence -1 (FR-30) |
| `mixed` | no accompaniment | auto-downgrade, `effective_mode = clean`, "cheaper and more accurate" (FR-29) |

Spec 6.5's stage table makes A4 (melody extraction) and A5 (direct pitch
detection) mutually exclusive per mode, and A9's timbre/breath aspects
structurally do not run at all in `mixed` (not merely score lower). Which
of these ran is decided once, before the pipeline starts, by
`PipelineRunner.run(mode=...)` filtering each stage's own `modes` (spec
12.3, ADR... this codebase's stage-mode mechanism).

Read literally, FR-29's "cheaper and more accurate" implies A3's finding
should retroactively change *which stages already ran* -- a `mixed`-
declared analysis that turns out to be a cappella should get A5's direct
pitch detection and the full six-aspect `clean` report, not settle for
whatever `mixed`'s narrower stage set already computed. But A3 (spec 6.5
table, stage 8 in this codebase's actual order) runs *after* A4/A5 and the
aspect stages, not before -- by the time its finding exists, the "wrong"
stages already ran and the "right" ones did not. Making A3's finding
actually change stage selection means either running A3 as an early,
separate pre-pass (splitting one job's pipeline run into two
`PipelineRunner.run()` calls, each with its own slice of `already_done` for
spec 6.8's resumability) or teaching `PipelineRunner` to re-filter its
remaining stages mid-run from a stage's own result. Both are real
`PipelineRunner`/job-handler architecture changes, not a stage-level one.

## Decision

For M3, A3's mode reconciliation is diagnostic only. `effective_mode`, the
warning (`ACCOMPANIMENT_IN_CLEAN_MODE`/`MODE_DOWNGRADED_TO_CLEAN`), and the
confidence step-down are all reported alongside whatever this run actually
computed under its *declared* mode -- they do not change which stages ran,
which weight profile applies, or which aspects are available. A `mixed`-
declared analysis that A3 finds is actually a cappella still reports
`mixed_v1`'s four aspects, still used melody extraction for pitch/vibrato;
it additionally reports `effective_mode: "clean"` and
`MODE_DOWNGRADED_TO_CLEAN`, an honest signal the user acts on by retrying
explicitly with `mode=clean` (the same one-click retry FR-30 already
describes for the opposite case, clean-with-accompaniment).

This is a real, measurable gap from FR-29's literal "cheaper and more
accurate" -- accepted for now given the runner-architecture cost above.

## Consequences

- No `PipelineRunner`/job-handler changes; A3 stays a single stage in the
  existing fixed order, exactly like every other stage.
- A `mixed`-declared, actually-a-cappella analysis pays A4's melody-
  extraction cost (spec budget 90s) it did not need to, instead of A5's
  (which spec 6.17 budgets far cheaper, ~18s) -- a real, accepted cost
  until this is revisited. Measured mitigation: melody extraction on a
  genuinely unaccompanied signal has no competing harmonic source for its
  background-subtraction step to suppress (`dsp/melody.py`), so accuracy
  on the four aspects it does cover should not be worse than A5's own,
  even though it is slower and leaves timbre/breath unavailable either way.
- The user-facing loss is exactly two aspects (timbre, breath) and a
  slower path than necessary -- not a wrong score. `effective_mode` and the
  warning make the gap visible rather than silently accepting a suboptimal
  result as final (spec G7).
- Revisit if this proves to be a common case in practice (spec 19: "Users
  don't understand the difference between modes and always pick `mixed`"
  is already a named risk) -- at that point the two-phase pipeline-run
  split described above is the concrete next step, not a redesign from
  scratch.

## Alternatives considered

- **Split A1/A2/A3 into an early pre-pass, run the rest with
  `mode=effective_mode`** -- rejected for M3: touches `PipelineRunner`'s
  resumability contract (spec 6.8, `already_done` partitioning across two
  runner instances) and the WS progress protocol's stage-count assumptions
  (spec 8.5), for a scope the M3 acceptance criteria (spec 18: spike +
  A3/A4/A8 + weight profiles + confidence model) do not name. Worth
  revisiting as its own ADR if the risk above materializes.
- **Teach `PipelineRunner` to re-filter remaining stages from a mid-run
  stage result** -- rejected: a much larger, more general change to a
  component (`PipelineRunner`) that spec 12.3 explicitly wants to stay
  simple ("adding a stage must not require editing the runner"); a
  purpose-built two-phase split (above) is the smaller, more targeted fix
  if this is revisited.
- **Ignore A3's finding entirely for `mixed` (drop FR-29's downgrade)** --
  rejected: the diagnostic value (accurate `effective_mode`, the warning,
  the confidence step-down, and the actionable retry suggestion) is real
  and cheap to provide even without the stage-selection change; only the
  literal "cheaper" half of FR-29 is deferred, not the "more accurate"
  signal or FR-30's symmetric clean-with-accompaniment case (which needed
  no stage-selection change to begin with -- `clean`'s full stage set
  already ran).
