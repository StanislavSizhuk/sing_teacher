# ADR-0022: Dense numeric curves stored as `bytea` float32, not JSONB

- Status: Accepted
- Date: 2026-07-30

## Context

Spec 7.3 flags a concrete cost: a 3-minute pitch curve at a 10ms hop is
~18,000 points, which as JSONB text runs to hundreds of KB per analysis and
needs parsing on every read. Before this change the pipeline stored dense
curves in three places, two of them already JSONB
(`songs.reference_pitch_json`, `analyses.pitch_curve_json`) and one of them
worse: `pitch`'s full `StageResult` -- including both the user's and the
reference's complete per-frame curve, `user_pitch_curve` and
`reference_pitch_curve`, plus the already-duplicated `piano_roll` -- was
merged into `analyses.stages_json` by `PipelineRunner.save_stage_progress`
like every other stage's result, and stayed there permanently once the
analysis finished. The reference curve alone was effectively stored twice
per song's first analysis (once in `songs.reference_pitch_json`, again
inside that analysis's `stages_json`); the user's curve was stored once,
but only inside `stages_json`, never in a column of its own.

## Decision

- `songs.reference_pitch_json` (JSONB) becomes `songs.reference_pitch`
  (`bytea`, packed `float32` little-endian) + `songs.reference_pitch_meta`
  (JSONB sidecar: `hop_seconds`, `length`, encoding tag) --
  migration `00009_dense_curves_as_bytea.sql`.
- `analyses` gains `user_pitch` (`bytea`) + `user_pitch_meta` (JSONB), the
  user's own dense curve, previously only reachable by parsing it back out
  of `stages_json`.
- `PitchCurve.to_bytes()`/`.from_bytes()` (`worker/src/vocalcoach/models/audio.py`)
  do the packing: `None` (unvoiced) becomes `NaN`, the standard float
  sentinel for "no value", decoded back to `None` on read.
- Once `save_piano_roll`/`save_user_pitch_curve`/`SongRepository.mark_vocal_stem_processed`
  have durably written their columns, `AnalysisJobHandler` calls a new
  `AnalysisRepository.prune_dense_stage_fields`, which strips
  `user_pitch_curve`/`reference_pitch_curve`/`piano_roll` back out of the
  persisted `stages_json` (a targeted `jsonb #-` delete, not a full
  rewrite). `stages_json`'s per-stage write stays exactly as it was
  *during* a run -- that is spec 6.8's resumability mechanism, and a
  `failed` analysis that might still retry keeps its full stage data for
  exactly that reason -- the prune only runs once an analysis reaches
  `done` and will never be resumed again.
- `analyses.pitch_curve_json` (the UI-facing piano-roll overlay) is
  intentionally **not** touched by this ADR: spec 7.3 keeps it as JSONB by
  design ("Дані для UI ... прорідена крива"); thinning it to ~2000 points
  is a separate, not-yet-done piece of spec 7.3 this ADR does not claim to
  cover.

## Consequences

- The two genuinely duplicated/uncolumned dense curves (reference pitch,
  user pitch) are stored once each, as packed bytes, with no JSON parsing
  on read.
- `stages_json` no longer grows unboundedly with per-frame data once an
  analysis is `done`; it stays JSONB-appropriate (small per-stage summaries:
  scores, counts, flags) for its actual, indefinite retention (spec 7.2:
  "Результати аналізу... безстроково").
- Go's `domain.Song.ReferencePitch` field scans the same `[]byte` type
  whether the column is `jsonb` or `bytea` (`pgx` returns raw bytes for
  both), and nothing on the Go side ever parsed this column as JSON --
  `api/internal/repository/postgres/song_repository.go`'s change is a
  column-name rename only.
- A rollback (`goose down`) restores the JSONB columns as `NULL` -- no
  attempt is made to losslessly convert bytes back to the old JSON shape,
  since nothing downstream reads through a rollback path live (this
  project has no production data yet to preserve across the migration).

## Alternatives considered

- **Thin `pitch_curve_json` to ~2000 points too, in the same change** --
  deferred: changes what the frontend's piano-roll receives (array length,
  possibly resampling logic), a separate risk surface from the pure
  storage-format change this ADR makes; tracked as follow-up, not folded in
  here to keep this change's blast radius to the backend/DB only.
- **Keep the reference/user curves in `stages_json`, only add `bytea`
  columns as a cache** -- rejected: leaves the exact duplication spec 7.3
  and 6.20's anti-pattern list call out unresolved, for no benefit over
  pruning once the durable copy exists.
