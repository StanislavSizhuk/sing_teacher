# ADR-0004: DTW as the time-alignment method

- Status: Accepted
- Date: 2026-07-27
- Note: the ML pipeline is built in E3, not E1. This ADR exists now because
  spec 14.3 requires it to already be on record at stage 1.

## Context

A user's recording is essentially never perfectly time-synchronized with the
reference: different start latency, and tempo/phrasing drift throughout.
Every downstream stage -- pitch, rhythm, vibrato, dynamics -- needs a shared
time mapping before it can compare anything (spec 6.3).

## Decision

Dynamic Time Warping (`dtw-python`) aligns the user's vocal to the reference
vocal. Every later stage consumes DTW's time mapping rather than raw
wall-clock offsets.

## Consequences

Robust to tempo drift and rubato within a bounded warping window, and gives
one place to fix alignment quality rather than six. The tradeoff: DTW can
misbehave under large tempo divergence or dropped/repeated phrases. Mitigated
by a bounded warping window, a word-level fallback using Whisper's
timestamps, and a distinct `ALIGNMENT_FAILED` error code (spec 6.8, 19)
rather than silently producing garbage scores.

## Alternatives considered

- Global cross-correlation offset -- rejected: corrects only a constant time
  shift, not phrase-by-phrase tempo variation in a real performance.
- Manual/UI-assisted alignment (user marks beat 1) -- rejected: adds friction
  to the "record and go" golden path (FR-20/FR-22) for a problem DTW already
  solves automatically in the common case.
