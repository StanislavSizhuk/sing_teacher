# Dev Log

Weekly status for the tech lead: done / next / blockers / risks, ~10 lines
(spec 14.4).

## 2026-07-27 -- E1

**Done:** Auth (email+password with 6-digit verification, Google
OAuth2/PKCE), full DB schema (users/songs/analyses/progress_snapshots) via
goose migrations embedded in the binary, Go API skeleton
(domain/service/repository/transport), Docker Compose for prod + dev, CI
(lint/test/security/commit-lint/build, no CD yet), 5 required ADRs + one for
the Go dependency choices made this stage.

**Next:** E2 -- song upload/YouTube import, browser recording, job queue with
live position (Redis Streams).

**Blockers:** none.

**Risks:** Google OAuth needs real `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
to test end-to-end (not yet registered with Google); email+password path is
fully tested without it. No production VPS targeted yet -- deploy automation
is E6.

## 2026-07-28 -- E2

**Done:** Song upload + YouTube import (shared sniff/probe/transcode/dedup
pipeline), Redis Streams analysis queue with Postgres-computed FIFO
position (ADR-0008), `429` on queue-full/rate-limit, WebSocket position
channel, cancel-while-queued, retry-when-failed (FR-26, unit-tested,
unreachable until E3). go-api runtime moved to Alpine for ffmpeg/yt-dlp
(ADR-0007). `web/` SPA now covers all of it: auth, add-a-song,
MediaRecorder recording, live queue status. CI gained web lint/test/
security/build jobs.

**Next:** E3 -- ML pipeline, which closes retry's remaining gap (a worker
that can actually produce a `failed` job) and lets audio deletion switch
from the interim age-based sweep to real processing completion.

**Blockers:** none.

**Risks:** `web/` isn't wired into Caddy/compose for production -- still a
Vite dev server pointed at go-api directly; that's deploy/CD work. Google
sign-in has no UI yet. YouTube import untested against the real service (no
live network calls in tests, by design) -- worth a manual smoke test.

## 2026-07-28 -- E3

**Done:** Python ML worker, 10 stages (Demucs, Whisper, DTW align, pitch/
rhythm/vibrato/dynamics/timbre/breath), each its own child process for a
real per-stage timeout and Demucs/Whisper never resident together
(ADR-0012). Redis Streams consumer with reclaim/give-up (spec 10.1),
Postgres repositories, Redis Pub/Sub relay into `go-api`'s WS channel
(ADR-0010). Retry (FR-26) reachable end-to-end for the first time. 68
Python tests + 8 new Go tests, all green; CI gained worker jobs. 3 new
ADRs, `docs/ML_PIPELINE.md`.

**Next:** E4 -- stage 11 (weighted `overall_score`, `scoring_version`), the
text report, piano-roll UI.

**Blockers:** none.

**Risks:** every empirical threshold (alignment ceiling, vibrato/breath
detection, quiet-reference floor) is a starting value, not one calibrated
against real singing (spec 19 already expects this). Never run against
real Demucs/Whisper inference end-to-end here (weight-download cost);
tests fake those two stages by design (spec 15.2) -- worth a manual smoke
test on real hardware before E4.
