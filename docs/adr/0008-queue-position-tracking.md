# ADR-0008: Queue position computed in Postgres, not read back from Redis Streams

- Status: Accepted
- Date: 2026-07-28

## Context

Spec 10 asks for three things: an instant position estimate on submission,
live position updates as the queue changes, and a hard cap that returns
`429 QUEUE_FULL` past `QUEUE_MAX_LENGTH`. ADR-0002 already commits to Redis
Streams as the queue's delivery mechanism. Redis Streams gives `XLEN` for a
cheap total count, but it has no cheap way to answer "what is entry X's rank
among entries not yet delivered" without consumer-group bookkeeping
(`XPENDING`/`XACK` state per entry), and no cheap way to reassign every
other queued job's position when one is removed out of order (a cancel,
FR-25) -- Streams are an append log, not an ordered, mutable set.

## Decision

`analyses.queue_position` is the number the API and WebSocket clients see,
and it is computed and stored in Postgres: a new `queue_seq BIGSERIAL`
column gives a stable FIFO order, and
`AnalysisRepository.RecalculatePositions` reassigns `1..N` to every row with
`status = 'queued'`, ordered by `queue_seq`, via a single
`ROW_NUMBER() OVER (ORDER BY queue_seq)` update that returns only the rows
whose position actually changed. This runs after every enqueue and cancel.
Redis Streams keeps its ADR-0002 job: `XADD` at enqueue, `XLEN` for the
overflow check, `XDEL` (best-effort) to remove a canceled entry so `XLEN`
stays accurate, and (in E3) `XREADGROUP`/`XACK`/`XAUTOCLAIM` for the worker.
`queue_stream_id` on the analysis row remembers the exact entry id XADD
returned, so cancel can XDEL precisely instead of scanning the stream.

## Consequences

Position math becomes an ordinary SQL query against a table already indexed
for it (`analyses_queue_seq_idx`), reusable for the WebSocket push, the REST
fallback (`GET /analyses/{id}`), and any future history/monitoring view --
no separate Streams-reading code path needed for something that is
fundamentally "where does this row rank." The tradeoff is two sources of
truth for queue state instead of one: Postgres for position/status,
Redis Streams for delivery. Correctness depends on `queue_stream_id` and
`status` staying consistent, which `AnalysisRepository.Cancel` and
`AnalysisService.Enqueue` keep true by construction (Postgres write, then
Redis write, in that order, so a Redis failure never claims a job is queued
when it isn't in the stream -- see `service/analysis/enqueue.go`).

## Alternatives considered

- Compute position purely from `XRANGE` + per-entry `XPENDING` state --
  rejected: requires tracking delivery/ack state per entry to know which
  ones are "still waiting," which Streams does not expose as a cheap
  ordered query, and still can't reassign positions on an out-of-order
  removal without extra bookkeeping equivalent to what Postgres already
  does natively with an index.
- Trim/re-`XADD` the stream on cancel to keep it dense -- rejected: XADD IDs
  are monotonic and not renumberable; "compacting" the stream means
  deleting and recreating entries, which is more complex than one UPDATE
  and reintroduces the same "what's still queued" bookkeeping problem.
