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
