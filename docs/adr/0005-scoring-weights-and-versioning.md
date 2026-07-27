# ADR-0005: Scoring weights as config, versioned with every analysis

- Status: Accepted
- Date: 2026-07-27
- Note: aggregation is built in E4, not E1. This ADR exists now because spec
  14.3 requires it to already be on record at stage 1.

## Context

The overall score is a weighted sum of six sub-scores. The weights are a
product-tuning decision that will change as real users' results are
calibrated against felt fairness (spec 19 names this a real risk), and
changing them must not silently rewrite what every past score meant.

## Decision

Weights (pitch .35, rhythm .20, breath .15, dynamics .10, vibrato .10,
timbre .10) live in config (`SCORING_WEIGHTS`), not as a Go/Python constant.
Every analysis persists the `scoring_version` string and `model_versions` it
was computed with (schema, spec 7).

## Consequences

Weights can be retuned without a code change or a redeploy of scoring logic.
Old analyses stay reproducible and explainable after the formula changes --
"this was scored under v1.0" -- rather than becoming ambiguous. The cost is
one more thing to validate at startup (weights must sum to 1 and reference
only known aspects) and one more column to populate on every write path.

## Alternatives considered

- Hardcoded weights as named constants -- rejected: spec 12.1 explicitly
  forbids magic numbers for exactly this kind of tunable value, and a pure
  tuning change shouldn't require a code change and redeploy.
- Recompute-in-place with no versioning -- rejected: makes the progress
  chart (FR-35) retroactively meaningless every time weights are tuned,
  undermining goal G4.
