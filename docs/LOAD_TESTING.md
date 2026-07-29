# Load testing

Stage E6's acceptance criterion (spec 18): **20 concurrent analysis
submissions must not crash the server**, and the queue must cap admission at
`QUEUE_MAX_LENGTH` instead of accepting everything. `api/cmd/loadtest` is a
small Go CLI that proves this against a real, running stack over real HTTP
-- not a mock, not an in-process test.

There is no staging/test server for this project by design (spec 16.3), so
this tool is meant to run **locally**, against your own
`docker-compose.dev.yml` stack.

## What it does

1. Checks `/healthz` and `/readyz` before starting.
2. Registers, verifies (reading the code back from mailhog) and logs in
   `-concurrency` distinct throwaway accounts -- one request per user, so
   the per-user rate limit (`USER_ANALYSES_PER_HOUR`) never contaminates the
   result with a 429 that isn't actually about queue capacity.
3. Uploads one shared synthetic reference song (a generated sine-wave WAV,
   built in memory -- no real audio fixture needed or committed, spec 15.2).
4. Fires all `-concurrency` `POST /analyses` requests behind a single
   start barrier, so they leave as close to simultaneously as one process's
   goroutines can manage.
5. Checks `/healthz` and `/readyz` again.
6. Best-effort cancels every analysis it queued, so it doesn't leave a pile
   of jobs for a real worker to process afterward.

A request either comes back `202 Accepted` or `429` with
`code: QUEUE_FULL`. Anything else -- a connection failure, a timeout, a 5xx,
a 429 with a different code -- fails the run, on the theory that any of
those is evidence the server didn't handle the load cleanly.

## Running it

You need `postgres`, `redis`, `mailhog` and `go-api` from the dev stack.
`python-worker` is optional: the tool never waits on it, and leaving it out
keeps the queue from draining mid-burst, which makes the QUEUE_FULL boundary
easier to hit reliably.

```bash
cp .env.example .env   # if you haven't already; fill in a GOOGLE_CLIENT_ID/SECRET placeholder, config.Load() requires non-empty values even though dev never calls Google
docker compose -f deploy/docker-compose.dev.yml up -d postgres redis mailhog go-api
```

Wait for `go-api` to log `listening` (`docker compose -f deploy/docker-compose.dev.yml logs -f go-api`), then:

```bash
cd api
go run ./cmd/loadtest
```

Example output:

```
2026/07/29 07:58:17 checking server health before the burst
2026/07/29 07:58:17 provisioning 25 verified test users via http://localhost:8025
2026/07/29 07:58:23 uploading a shared reference song
2026/07/29 07:58:23 firing 25 concurrent POST /analyses requests
2026/07/29 07:58:23 burst results: 25 total, 20 accepted (202), 5 rejected as QUEUE_FULL (429), 0 transport failures, 0 unexpected responses, max latency 307.871264ms
2026/07/29 07:58:23 checking server health after the burst
2026/07/29 07:58:23 cleaning up: canceling every analysis this run queued
2026/07/29 07:58:24 load test passed
```

20 accepted + 5 rejected, against the default `QUEUE_MAX_LENGTH=20` -- the
queue admitted exactly its cap under a real concurrent burst and rejected
the rest cleanly, and the server answered every single request throughout.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `-base-url` | `http://localhost:8080` | `go-api` root (not `/api/v1`) |
| `-mailhog-url` | `http://localhost:8025` | mailhog, for reading verification codes |
| `-concurrency` | `25` | requests fired in the burst; must exceed the server's `QUEUE_MAX_LENGTH` to actually exercise the 429 boundary, not just prove "20 requests didn't crash it" |
| `-timeout` | `3m` | overall run timeout |

Run it a few times, and at higher `-concurrency` (say 50 or 100), to get a
feel for how the queue behaves well past its cap -- the pass/fail gate
(`summary.validate` in `api/cmd/loadtest/summary.go`) doesn't hardcode
`QUEUE_MAX_LENGTH`; it only requires that every request landed in exactly
one of the two expected outcomes and the server never dropped a connection.

## What this is not

This is not an ML throughput benchmark -- `python-worker` is deliberately
left out or ignored, and the synthetic recording is a plain sine wave never
meant to produce a meaningful score. It is a concurrency/capacity test of
`go-api`'s HTTP and queue-admission path, which is the part spec 18/E6 asks
to be proven under load.

## What it found

The first version of this tool reliably tripped a real bug: the queue's
admission check (`Length()` then `Enqueue()`, two separate Redis calls) let
concurrent requests all read the same pre-publish queue length and all
decide to admit, overshooting `QUEUE_MAX_LENGTH`. Fixed in
`internal/queue.Producer.EnqueueIfUnderLimit` (an atomic Lua `EVAL`) --
see that commit and `internal/queue/producer_integration_test.go`'s
concurrent-burst test for the regression coverage against a real Redis
instance, independent of this CLI tool.
