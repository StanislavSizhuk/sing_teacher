# ADR-0002: Redis Streams as the job queue (instead of RQ/Celery/NATS)

- Status: Accepted
- Date: 2026-07-27
- Note: the queue itself is built in E2/E3, not E1. This ADR exists now
  because spec 14.3 requires it to already be on record at stage 1.

## Context

Analysis jobs need: visible queue position, consumer-group delivery to a
single worker replica, at-least-once semantics, and the ability to reclaim a
job whose worker died mid-stage (spec 6.8, 10.1). Redis is already a
required dependency (sessions, rate limiting); adding a different queue
technology means a second piece of infrastructure to run and monitor on the
one VPS this whole system lives on (spec 5.1).

## Decision

Redis Streams (`XADD`/`XREADGROUP`/`XACK`/`XAUTOCLAIM`) is the job queue.
`job_id = analysis_id`, so redelivery of the same stream entry can never
create a duplicate analysis.

## Consequences

No new service to operate -- Redis is already there. Consumer groups give
exactly the pending-entry/reclaim semantics NFR-04's single worker needs;
`XLEN` gives an O(1) queue-length for position reporting. The tradeoff is a
thinner ecosystem than Celery/RQ (no admin UI, no scheduling DSL), which is
acceptable at the scale this spec targets: one worker, a 20-job queue cap.

## Alternatives considered

- Celery + RabbitMQ/Redis broker -- rejected: RabbitMQ (or Celery's own
  operational surface) is disproportionate infrastructure for one worker and
  twenty queued jobs.
- RQ -- rejected: still a separate dependency and worker-process model
  layered on top of Redis, when Streams already provides consumer groups
  natively.
- NATS/JetStream -- rejected: a genuinely new infrastructure dependency,
  against the "everything on one VPS, minimum footprint" constraint (5.1).
