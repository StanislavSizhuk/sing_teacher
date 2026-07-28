# ADR-0010: E3 worker relays live status to the API over Redis Pub/Sub

- Status: Accepted
- Date: 2026-07-28

## Context

Spec 8.3 wants live `stage`/`done`/`failed` events on the WebSocket channel,
not just `queued` position updates (already built in E2). Those events
originate inside the Python worker (E3), a separate OS process from
`go-api` -- the process that actually owns `ws.Hub` and every open
WebSocket connection. The worker has no way to call `Hub.Broadcast*`
directly; it needs some channel to cross the process boundary.

## Decision

The worker publishes one JSON message per event to a Redis Pub/Sub channel
(`analyses:events`, `queue/events.py`'s `RedisEventPublisher`). `go-api`
subscribes to that channel once at startup (`internal/queue.RelayEvents`,
run in its own goroutine) and forwards each decoded message into
`Hub.BroadcastStage`/`BroadcastDone`/`BroadcastFailed`. A malformed message
is logged and dropped, never fatal to the relay goroutine -- WebSocket is
already spec'd as a best-effort transport (8.3): REST (`GET
/analyses/{id}`) is the source of truth a client falls back to regardless.

Redis is already a required dependency for three other roles (sessions,
rate limiting, the Streams job queue, ADR-0002); Pub/Sub is a fourth,
built into the same server, not a new piece of infrastructure.

## Consequences

The worker and API stay fully decoupled -- the worker never needs to know
`go-api`'s address, and `go-api` never needs to poll Postgres for progress
just to relay it onward. The cost is an at-most-once delivery guarantee:
Redis Pub/Sub drops a message for any subscriber that was not connected at
publish time (a `go-api` restart mid-analysis loses whatever events fired
during the gap). This is acceptable because the WS channel was already
lossy-by-design (8.3): the REST fallback always has the correct final
state, and a missed `stage` event only costs a client one intermediate UI
update, not correctness.

## Alternatives considered

- Worker writes progress to Postgres only, `go-api` polls it to build WS
  events -- rejected: reintroduces the polling latency/DB-load tradeoff the
  WebSocket channel exists to avoid, for the specific data (`stages_json`,
  `current_stage`) that spec 6.1 already puts in Postgres for a different
  reason (resumability), not for push notification.
- Worker calls a `go-api` HTTP endpoint to trigger the broadcast --
  rejected: makes the worker depend on `go-api`'s network address and
  availability, backwards from the existing dependency direction (worker
  reads/writes Postgres and Redis directly; it does not call the API).
- Redis Streams (a second stream, `analyses:events`) instead of Pub/Sub --
  rejected: events are fire-and-forget UI updates with no replay or
  at-least-once requirement (unlike the job queue, ADR-0002); a consumer
  group's bookkeeping would be pure overhead here.
