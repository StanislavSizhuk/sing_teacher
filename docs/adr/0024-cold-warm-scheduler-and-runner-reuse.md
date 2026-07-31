# ADR-0024: Priority-claim scheduling and a shared PipelineRunner for the cold/warm split

- Status: Accepted
- Date: 2026-07-31

## Context

Spec 6.2/10 (TECH.md v2.0) splits what was one per-analysis pipeline into
a **cold path** (P1-P4: reference decode, Demucs separation, optional
transcription, reference pitch -- runs once per song, asynchronously, the
moment a song is added) and a **warm path** (A1-A11: everything that
depends on a specific user recording -- runs once per analysis, only once
the song's cold path is `ready`). Spec 6.5/NFR-04/NFR-07 still require
exactly one ML job in flight at a time (Demucs and Whisper must never be
resident together, and the whole point of the M1 performance pass was a
single worker replica doing one thing at a time, not concurrent jobs
racing each other for the same CPU). Spec 10.2 adds one more rule: given
two candidate jobs, `analyses:run` (warm) is served first, but a
`songs:prep` (cold) entry whose song has an analysis already waiting on it
should jump that stream's own FIFO -- a waiting user should not sit behind
an unrelated song's cold-path prep that nobody is blocked on yet.

Two design questions followed directly from this:

1. **How does one worker process, with one Redis Streams consumer group
   per stream, implement "warm first, then priority-or-FIFO cold" without
   either a second worker process or busy-polling both streams?** Redis
   Streams has no primitive to peek an arbitrary undelivered entry out of
   order -- `XREADGROUP ... >` always delivers the next entry in stream
   order to whichever consumer asks, and `XCLAIM`/`XAUTOCLAIM` only
   operate on entries already in a consumer's pending entries list (PEL),
   not on ones still undelivered. An entry cannot be "peeked and put
   back" undelivered.
2. **Does the cold path get its own copy of the orchestration machinery
   (timeout, retry, per-stage progress persistence) that the warm path's
   `PipelineRunner` already has, or does something get shared?** Spec
   12.1 (DRY) treats a second copy of orchestration logic as a bug
   waiting to happen, but `PipelineRunner` was written for one job kind:
   its progress-persistence calls took an `analysis_id` and wrote through
   `AnalysisRepository`.

## Decision

**Scheduler (`worker/src/vocalcoach/queue/scheduler.py`):** each tick,
`Scheduler._tick()` calls `Consumer.claim_new_entries()` on
`analyses:run` first. This does two things in one call: `XREADGROUP`
delivers every currently-undelivered entry into this consumer's own PEL
(cheap -- `QUEUE_MAX_LENGTH`/`songs:prep`'s own cap are both 20, so "every
entry" is never large), then `Consumer.pending_entries()`/`fields_for()`
(`XPENDING` + `XRANGE`) read that PEL back as an ordinary list the
scheduler *can* pick from arbitrarily. If `analyses:run` has anything
pending, its first (oldest) entry is processed and the tick ends there.
Otherwise the scheduler does the same claim-into-PEL step for
`songs:prep`, then chooses: if `AnalysesRepository.oldest_waiting_song_id()`
names a song, and that song has a pending `songs:prep` entry, that entry
is processed regardless of its position in the PEL (spec 10.2's priority
jump); otherwise the oldest pending entry is processed (plain FIFO). If
neither stream has anything pending, the scheduler falls back to a single
blocking `XREADGROUP` (`IDLE_BLOCK_MS = 2000`) on `analyses:run` as a wake
signal -- and if that blocking call actually returns an entry (a job
arrived during the wait), it is processed immediately rather than
discarded, since a successful blocking `XREADGROUP` has already delivered
it into this consumer's PEL; throwing it away and re-looping would strand
it there for up to `SONGS_PREP_PENDING_CLAIM_MIN_IDLE` (20 minutes) before
the reclaim sweep would pick it back up.

**Shared runner (`pipeline/runner.py`, `pipeline/base.py`):**
`PipelineStage`/`ParallelGroup`/`PipelineRunner` become generic over a
`ContextT` bound to a `PipelineContext` protocol (`result`/`with_result`),
so the same classes drive both `AnalysisContext` (warm) and
`SongPrepContext` (cold). `PipelineRunner` no longer depends on a
job-kind-specific repository; it depends on a narrow `ProgressReporter`
protocol (`mark_processing`, `save_stage_progress`, neither call takes a
job id). `AnalysisProgressReporter` and `SongPrepProgressReporter`
(`queue/handler.py`, `queue/prep_handler.py`) each close over one job's id
and adapt its own repository methods to that protocol. `AnalysisJobHandler`
and `SongPrepJobHandler` each own a `PipelineRunner` instance built from a
different stage list (`worker.build_stages` vs. `worker.build_prep_stages`)
but otherwise drive it identically.

## Consequences

The scheduler's claim-then-select approach means every tick does at least
one `XREADGROUP` against `analyses:run` even when it is empty -- a cheap
round trip (the stream is capped at 20 entries), not a concern at this
scale, but worth naming as the mechanism's actual cost: it is a poll with
a very cheap poll body, not a push. Priority selection also means a
`songs:prep` entry can sit claimed-but-unprocessed in this consumer's PEL
across several ticks while `analyses:run` keeps producing higher-priority
work; that is fine (nothing else is competing for it, spec NFR-04) but
means `XPENDING` alone cannot answer "how long has this actually been
waiting" -- `fields_for()` reads the original enqueue time out of the
entry's own fields for that, not Redis' delivery-time bookkeeping.

The shared `PipelineRunner` keeps timeout/retry/optional-stage/resumability
logic in exactly one place for both paths, at the cost of one extra
indirection layer (the two `ProgressReporter` adapters) that a
single-job-kind runner would not need. This is the DRY trade spec 12.1
asks for: the alternative (a second `PrepPipelineRunner` copy-pasted from
`PipelineRunner`) was rejected outright -- the two would drift the moment
someone fixed a retry-backoff bug in only one of them, exactly the "rule
that exists in two places" CLAUDE.md's non-negotiable DRY principle warns
about.

One more cross-language duplication follows from spec 10.3's wake-up
requirement, not from this ADR's own decisions, but is worth recording
here since it is the same class of tradeoff: `SongPrepJobHandler` needs to
promote every `waiting_for_reference` analysis of a now-`ready` song to
`queued` and recompute their positions, and `AnalysisRepository`'s own
`RecalculatePositions` (ADR-0008) already does the equivalent
`ROW_NUMBER() OVER (ORDER BY queue_seq)` query, but in Go, against a
connection the Python worker does not share. `wake_waiting_for_reference`
(`repositories/postgres.py`) is a deliberate byte-for-byte SQL mirror of
that query rather than an HTTP call back into `go-api` or a shared SQL
file included by both languages' build systems -- neither of those exists
in this stack, and inventing one for a single six-line query was judged
not worth the machinery. The risk this accepts: if `RecalculatePositions`'
query ever changes, `wake_waiting_for_reference` has to change with it by
hand, with no compiler or test to catch a missed update except the T11
integration test asserting both produce the same position ordering on the
same fixture data.

This M2 pass does not touch `align.py`'s DTW or its MFCC-based feature
comparison at all -- `mixed`-mode melody extraction/chroma features (spec
6.6, M3) stay explicitly out of scope here; the cold/warm split changes
*when* and *where* `align` runs relative to Demucs/Whisper, never what it
computes.

## Alternatives considered

- **A second worker process, one per stream** -- rejected: spec NFR-04/
  NFR-07 and spec 6.5 both assume a single ML job in flight (Demucs and
  Whisper must never be resident together); two processes each capable of
  loading a heavy model reintroduces exactly the memory-coexistence risk
  spec 6.5 exists to prevent, for a problem (priority scheduling) that a
  single-process scheduler already solves.
- **Poll `XLEN`/`XPENDING` counts only, without claiming into the PEL
  first** -- rejected: counts alone cannot answer "which specific pending
  entry has priority," and Streams provides no cheaper way to inspect an
  undelivered entry's fields than delivering it.
- **A single unified stream carrying both job kinds, discriminated by a
  `kind` field** -- rejected: `analyses:run`/`songs:prep` need different
  consumer-group reclaim thresholds (15 min vs. 20 min, since a single
  P-stage can run far longer than any single warm-path stage) and
  different producer-side queue-length caps enforced independently
  (`QUEUE_MAX_LENGTH` for analyses, spec 10.1's own cap for songs); one
  stream would need per-entry-kind branching for both of those anyway,
  with none of the benefit.
- **A separate `PrepPipelineRunner` class, copy-pasted and adjusted** --
  rejected as the DRY violation described above.
