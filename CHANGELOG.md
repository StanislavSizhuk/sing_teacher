# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows SemVer once tags start (spec 13.5); nothing has been
tagged yet.

## [Unreleased] -- E1

### Added

- Email+password registration with 6-digit email verification (24h expiry,
  60s/5-per-day resend limit) and Google OAuth2/PKCE sign-in.
- Session management: 15-minute JWT access tokens, rotating refresh tokens
  in an httpOnly cookie with reuse detection and family-wide revocation,
  logout and logout-everywhere.
- Account deletion with cascading data removal.
- Full database schema (`users`, `songs`, `analyses`, `progress_snapshots`)
  via goose migrations, applied automatically on API boot.
- Go API skeleton: layered `domain`/`service`/`repository`/`transport`,
  RFC 9457 error responses, structured JSON logging, `/healthz`/`/readyz`.
- `api/openapi.yaml` contract for every endpoint above.
- Docker Compose for production (Caddy, go-api, Postgres, Redis, nightly
  backup) and local development (hot reload, mailhog).
- CI: lint, unit + integration tests, security scanning (govulncheck, gosec,
  Trivy), commit-message validation, image build. No CD yet (spec 16.3: no
  staging; deploy automation is stage E6).
- ADRs 0001-0006.

## [Unreleased] -- E2

### Added

- Song ingestion: file upload and YouTube import (`yt-dlp`) sharing one
  sniff/probe/transcode/dedup pipeline; duration checked before download for
  YouTube (FR-12); content-hash/video-id dedup reuses an existing song
  (FR-13).
- Analysis job queue: Redis Streams with a Postgres-computed FIFO position
  (ADR-0008), live position over `GET /ws/analyses/{id}`, `429` on a full
  queue or an exceeded per-user rate limit, cancel-while-queued (FR-25), and
  retry-when-failed (FR-26, built and unit-tested; unreachable end-to-end
  until the E3 worker exists).
- `web/`: React + TypeScript (strict) + Tailwind v4 SPA covering all of the
  above -- register/verify/login, add a song, record in the browser
  (MediaRecorder) or upload a file, and a queue screen with live position
  (WebSocket, REST-poll fallback). One generated-from-`openapi.yaml` typed
  network layer with silent-refresh-on-401 retry.
- go-api runtime moved to Alpine for `ffmpeg`/`yt-dlp` (ADR-0007).
- CI: `web` lint (tsc/eslint/prettier + OpenAPI-types drift check), test
  (vitest), security (`npm audit --audit-level=high`) and build jobs.

## [Unreleased] -- E3

### Added

- `worker/`: the Python ML pipeline. Stages 1-10 (spec 6.2) --
  preprocessing, Demucs vocal separation (cached per song), Whisper
  transcription (cached per song), DTW alignment, and pitch/rhythm/
  vibrato/dynamics/timbre/breath scoring against the reference, each
  persisted to `analyses.stages_json` as it completes.
- Every stage runs in its own spawned child process: a real, enforceable
  per-stage timeout, and the mechanism that satisfies spec 6.5's "Demucs
  and Whisper never resident together" (ADR-0012).
- Redis Streams consumer (`XREADGROUP`/`XACK`, reclaim of a stuck job past
  15 minutes idle, giving up after repeated claims -- spec 10.1). Retry
  (FR-26) is reachable end-to-end now that a worker can actually produce a
  `failed` analysis.
- `go-api`: `Hub.BroadcastStage`/`BroadcastDone`/`BroadcastFailed` and a
  Redis Pub/Sub relay (`queue.RelayEvents`) forwarding the worker's events
  onto the existing WS channel (ADR-0010).
- `go-api`'s interim audio-retention sweep is now a 24h orphan safety net,
  not the primary FR-43 mechanism -- the worker deletes each file
  precisely when it's done with it.
- Docker Compose: `python-worker` service (prod: 6GB memory ceiling per
  NFR-07, 120s graceful-shutdown grace period; dev: source bind-mounted).
  New `song-stems` (persistent vocal-stem cache) and `model-weights`
  volumes.
- CI: `worker` lint (ruff, mypy --strict), test (unit + integration against
  real Postgres/Redis), security (`pip-audit`, Trivy image scan) and build
  jobs.
- ADRs 0010-0012 (worker events over Redis Pub/Sub, worker dependency
  choices, per-stage subprocess isolation); `docs/ML_PIPELINE.md`.

## [Unreleased] -- E4

### Added

- `worker/`: stage 11 (spec 6.3.11) weighted-sums the six aspect scores
  into `overall_score` via `SCORING_WEIGHTS`, stamped with
  `scoring_version` (spec 6.4, ADR-0005). `pipeline/report.py` builds the
  FR-32 text report from the same stage data -- one paragraph per aspect
  with concrete numbers, plus spec 6.3.9's mandatory timbre disclaimer.
- `worker/`: the pitch stage now also resamples the reference pitch curve
  onto the user's own time grid through the stage-4 DTW time map and
  precomputes a per-frame cents deviation and off-pitch flag (FR-31),
  persisted alongside the user's curve in `analyses.pitch_curve_json`.
- `go-api`: `GET /analyses/{id}` (and the enqueue/cancel/retry responses)
  now return the six aspect scores, `feedback_text`, `scoring_version`,
  and the piano-roll payload, passed through from the worker's JSON
  without a re-encode.
- `web/`: once an analysis is done, `QueueStatus` shows the full score
  breakdown (`AnalysisReport`) and a canvas piano-roll (`PianoRoll`) whose
  cursor tracks the user's own recording during playback (FR-33) -- the
  recording is replayed from the client-side Blob captured at record
  time, not re-fetched, since the server deletes it minutes after
  processing (FR-43).

### Known limitations

- The FR-32 report text is English-only; it is not routed through the
  web app's i18n key system, since it is dynamic prose over per-analysis
  numbers rather than static UI copy (see `docs/ML_PIPELINE.md`).
- Spec 6.9's non-vocal-energy warning is still unimplemented -- no stage
  owns it yet.

## [Unreleased] -- E5

### Added

- `progress_snapshots` is now written: the E3 worker upserts one row per
  analysis right after stage 11's `overall_score` is computed, keyed on
  `analysis_id` so a retried job updates its point instead of duplicating
  it (spec 6.8, FR-35). New migration adds the unique constraint that
  upsert relies on.
- `go-api`: `GET /progress` (FR-35) returns the caller's own points,
  oldest first, capped at a fixed size since it feeds a chart rather than
  a browsable list (spec 8.1's cursor pagination is for the latter).
- `web/`: a Progress screen -- summary stat tiles (latest/best/average/
  change), an accessible SVG line chart (`role="img"`, fixed 0-100 axis),
  and a visible session table, which is the chart's actual data source
  for anyone who can't read the line (mirroring how PianoRoll's
  `aria-label` works for the piano-roll). `App.tsx` gained a top-level
  Analyze/Progress nav and a skip-to-content link.
- `web/`: extracted `SegmentedControl`, the radiogroup-of-buttons pattern
  AddSongForm and RecordingCapture already used, now shared by the new
  nav too; `Button`/`SegmentedControl` gained an explicit keyboard focus
  ring.

### Known limitations

- FR-34 (a paginated `GET /analyses` history endpoint with song titles)
  is still not built -- the Progress screen's session table is fed by
  `progress_snapshots`, not a real history endpoint.

## [Unreleased] -- E6

### Added

- `api/cmd/loadtest`: a CLI that fires N concurrent `POST /analyses`
  requests at a running stack over real HTTP -- registers/verifies
  distinct users via mailhog, uploads a synthetic reference song, and
  asserts every response landed as either `202` or `429 QUEUE_FULL` with
  the server still healthy before and after (spec 18/E6's "20 concurrent
  tasks don't crash the server"). See `docs/LOAD_TESTING.md`.
- `deploy/deploy.sh`: checkout -> build+up -> poll `go-api`'s healthcheck
  for 60s -> automatic rollback to the previous ref if it never comes up
  (spec 16.2). Rehearsed locally, both the success path and a forced
  failure; no CD pipeline calls it, it's run by hand (spec 16.3: no
  staging server for a pipeline to target).
- `docs/RUNBOOK.md`: one-time VPS hardening checklist (UFW, SSH,
  fail2ban, Cloudflare -- spec 11.1) that previously existed only as
  spec text, never as an actual runbook step.
- `docs/SECURITY.md`: dated, evidence-checked walkthrough of every spec
  11 sub-item (11.1-11.6), stage E6's acceptance criterion.

### Fixed

- `internal/queue.Producer.EnqueueIfUnderLimit`: the analysis queue's
  admission check (`Length()` then `Enqueue()`, two separate Redis calls)
  let concurrent submissions all read the same pre-publish length and all
  decide to admit, overshooting `QUEUE_MAX_LENGTH` under a real burst --
  found by the load test above. Now one atomic Redis `EVAL`, with a
  Postgres-row rollback on the rare loss of that race.
- `deploy/docker-compose.dev.yml`: `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
  had no dev-only default, so `cp .env.example .env && docker compose up`
  did not actually work standalone as README/the compose file's own
  comment claimed -- `go-api`'s config validation failed fast on the
  empty values. Both now carry a placeholder like every other dev-only
  default already there.

### Known limitations

- Caddy's CSP/security headers (spec 11.2) only cover `go-api`'s own JSON
  responses today -- `web/`'s built static output still isn't served
  through Caddy in production (tracked since E2, out of scope for this
  CI-only stage). Re-verify those headers against real page content once
  that wiring lands. **Closed below.**

## [Unreleased] -- post-E6 fix

### Added

- `web/Dockerfile`: multi-stage build -- a `dev` target running the Vite
  dev server with hot reload, and a default target that builds the SPA and
  bakes it into Caddy's own image at `/srv/www` (ADR-0013).
- `docs/adr/0013-caddy-serves-web-build.md`.

### Changed

- `deploy/docker-compose.yml`: the `caddy` service now builds from
  `web/Dockerfile` instead of pulling the stock `caddy:2-alpine` image, so
  production serves the built SPA directly, from the same origin `/api/*`
  is proxied from -- closing the "frontend runs outside `docker compose`"
  gap tracked since E2 (spec 5.1/5.2, NFR-10).
- `deploy/docker-compose.dev.yml`: new `web` service (the `Dockerfile`'s
  `dev` target) on `:5173`, so `docker compose -f
  deploy/docker-compose.dev.yml up` alone now serves the frontend too,
  hot reload included -- no more separate `cd web && npm run dev` step.
- `deploy/Caddyfile`: serves `/srv/www` with a `try_files` SPA fallback,
  proxies `/api/*` and `/healthz`/`/readyz` to `go-api`. CSP tightened from
  `default-src 'none'` (correct only while Caddy proxied JSON exclusively)
  to a `self`-scoped policy covering the page content it now actually
  serves, with `blob:` allowed on `media-src` for `MediaRecorder` preview
  playback (FR-20).
- CI: `web`'s `lint`/`security`/`build` jobs now also build and Trivy-scan
  the production image, matching `api`/`worker`. Still CI-only -- no CD
  job added, no staging environment (spec 16.3).

### Fixed

- `security-web`'s first Trivy run against the new production image
  failed on 10 HIGH findings, none of them new to this change: the
  `caddy:2-alpine` digest already pinned in `deploy/docker-compose.yml`
  before this stage, just never scanned until `web/Dockerfile` gave it
  something to build and scan. 5 were Alpine packages --
  `curl`/`libcurl`/`c-ares` -- bundled in the base image but unused (the
  compose healthcheck runs busybox's `wget`, and the static Caddy binary
  doesn't link against them); `web/Dockerfile` now removes them outright.
  The remaining 5 are compiled into the upstream `caddy` binary itself
  (`golang.org/x/text`, `google.golang.org/grpc`, 3 in `stdlib`) -- no apk
  package to patch; `web/.trivyignore` documents, per CVE, why each
  doesn't apply to how this deployment actually uses Caddy, with a
  three-month expiry so the ignore starts failing the scan again instead
  of hiding a finding indefinitely.
- Real-song `transcribe` runs failed with `TIMEOUT` (spec 6.2's 180s
  budget), consistently: Whisper `small` on CPU with `word_timestamps=True`
  measured at 176.7-186s+ against a real ~4-minute song, right on top of
  its ceiling instead of under it. `WHISPER_MODEL` now defaults to `base`
  (ADR-0014, measured at 143.5s on the same song -- real margin) --
  exactly the fallback spec 19's risk table already prescribed for this
  measured outcome.

## [Unreleased] -- M4

### Added

- `POST /analyses` accepts `mode` (`clean`/`mixed`, FR-27, default
  `clean`) and an optional `allow_transposition` override (FR-31),
  validated and defaulted at the transport boundary; migration 00011
  stores them on the row alongside the worker's confidence/warnings/
  unavailable-aspects output (spec 6.14, 6.15) so `GET /analyses/{id}` and
  `GET /progress` can surface it without parsing `stages_json`.
- The worker builds each analysis's `AnalysisContext` from its own stored
  `mode`/`allow_transposition` instead of an always-`clean` default --
  `mode` selection is now wired end to end, not just worker-internal (M3).
- `web/`: a mode selector with a plain-language explanation of each mode's
  consequences shown before recording (FR-28); an unavailable aspect
  renders as "Not measured" with its reason, never a blank dash or a `0`
  (FR-41); a confidence badge and translated warning codes (FR-47); a note
  when the worker's `effective_mode` differs from what the user picked
  (FR-29/30); the applied key-shift-in-semitones line (FR-46); and a
  progress chart that shape-differentiates `clean`/`mixed` points with a
  legend and a comparability warning, since the two are scored under
  different `weights_profile` (FR-49).
- `docs/REVIEW_CHECKLIST.md`: a "Data honesty and mode UI" section for M4.
