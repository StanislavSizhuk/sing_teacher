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

**Done:** Song upload + YouTube import (sniff/probe/transcode/dedup
pipeline shared by both sources), Redis Streams analysis queue with
Postgres-computed FIFO position (ADR-0008) and `429 QUEUE_FULL`/rate-limit
rejection, WebSocket queue-position channel, cancel-while-queued. go-api
runtime moved from distroless to Alpine to host ffmpeg/yt-dlp (ADR-0007).
CI unchanged (still no CD). No frontend work this stage (scoped out
deliberately -- `web/` bootstrap deserves its own pass, see E5).

**Next:** E3 -- ML pipeline (Demucs, Whisper, DTW, pitch/rhythm/vibrato/
dynamics/timbre/breath), which also closes two E2 gaps: analysis retry
(FR-26, currently unreachable with no failure path) and audio deletion tied
to actual processing completion instead of the interim age-based sweep.

**Blockers:** none.

**Risks:** `web/` still doesn't exist, so FR-20/21 (browser recording,
recording upload) have no UI yet -- the API surface for them
(`POST /analyses`) is ready and tested, but unexercised by any real client
until E5. YouTube import is unverified against real YouTube in this
session (no network calls to the live service in tests, by design) --
worth a manual smoke test before relying on it.
