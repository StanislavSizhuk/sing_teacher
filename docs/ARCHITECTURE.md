# Architecture

Status: reflects stages E1-E5, including `web/`, plus the post-E6 fix that
wires `web/` into `docker compose` (ADR-0013), and a further post-E6 audit
pass that made the ML pipeline actually complete a real analysis end to
end for the first time (spawn-boundary pickling, Demucs model loading, the
retry/work-dir interaction, a Redis Streams resilience gap) plus the spec
6.9 recording-condition stage. Components and flows still planned for
later stages (Google sign-in UI, a paginated analysis history endpoint)
are noted as such, not described as if they existed.

## Components (target end-state, spec 5.2)

```
                    ┌────────────┐
   Browser ────────►│ Cloudflare │  DNS proxy, DDoS, hides origin IP
                    └─────┬──────┘
                          │ HTTPS
                    ┌─────▼──────┐
                    │   caddy    │  auto-TLS, reverse proxy + static React
                    └─────┬──────┘
                          │
                    ┌─────▼──────┐        ┌──────────┐
                    │   go-api   │◄──────►│  redis   │  sessions, throttles,
                    │  REST + WS │        └────┬─────┘  queue, event relay
                    └─────┬──────┘             │ XADD/XREADGROUP/pub-sub
                          │                ┌───▼──────────┐
                    ┌─────▼──────┐         │ python-worker│  ML pipeline (E3)
                    │  postgres  │◄────────┤  (1 replica) │
                    └────────────┘         └───┬──────────┘
                    ┌───────────┐   ┌──────────▼───────────┬──────────────┐
                    │  backup   │   │ audio-tmp: recording/│ song-stems,  │
                    │ nightly   │   │ reference, shared     │ model-weights│
                    │ pg_dump   │   │ w/ go-api (spec 7.2)  │ (persistent) │
                    └───────────┘   └───────────────────────┴──────────────┘
```

Built in E1: `caddy`, `go-api` (auth), `postgres`, `redis`, `backup`. Built
in E2: song upload/YouTube import, the Redis Streams job queue, the
WebSocket status channel, and `web/`, the React SPA that exercises all of
it. Built in E3: `python-worker` -- everything in
`docs/ML_PIPELINE.md` -- and the Redis Pub/Sub relay
(`internal/queue.RelayEvents`, ADR-0010) that lets `go-api` push the
worker's stage/done/failed events onward over the same WS channel E2 built
for queue position. Built in E4: stage 11 score aggregation and the FR-32
report, `GET /analyses/{id}` returning the full score breakdown and
piano-roll, and `web/`'s report/piano-roll screens. Built in E5:
`progress_snapshots` actually gets written (the worker upserts one row per
analysis alongside stage 11's result), `GET /progress` reads it back, and
`web/` gained a Progress screen (chart, stat tiles, session table) and a
top-level Analyze/Progress nav. Post-E6, `web/` was wired into `docker
compose` (ADR-0013): `deploy/docker-compose.yml`'s `caddy` service now
builds from `web/Dockerfile`, which bakes the built SPA into Caddy's own
image at `/srv/www`, and `deploy/Caddyfile` serves it from the same origin
it proxies `/api/*` to `go-api` from. `deploy/docker-compose.dev.yml` gets
a `web` service instead, running the same `Dockerfile`'s `dev` target (the
Vite dev server, hot reload) on `:5173`.

## go-api internal layers

```
transport/http, transport/ws  →  service/{auth,song,analysis}  →  repository/{postgres,redisrepo}, queue
                                          ↓
                                       domain
```

- `domain`: `User`/`Song`/`Analysis` entities and sentinel errors
  (`ErrNotFound`, `ErrInvalidCredentials`, `ErrQueueFull`, ...). Knows
  nothing about HTTP, SQL or Redis.
- `service/auth`: registration, email verification, login, refresh-token
  rotation, Google sign-in, account deletion. Declares every external
  dependency as an interface (`UserRepository`, `RefreshTokenStore`,
  `Mailer`, `PasswordHasher`, `AccessTokenIssuer`, `LoginThrottle`,
  `VerificationThrottle`, `GoogleVerifier`, `Clock`) -- the consumer owns the
  interface, per spec 12.2.
- `service/song`: `AddFromUpload`/`AddFromYouTube` both run
  sniff → probe → transcode → hash → dedup (spec 6.6, FR-10..14). YouTube's
  cache key (video id) is known from metadata alone, so a repeat submission
  never re-downloads.
- `service/analysis`: `Enqueue` validates the recording the same way, then
  creates the analysis row, publishes it to the Redis Streams queue, and
  recomputes every queued job's position (spec 10, FR-22). `Cancel` and
  `Retry` (FR-25, FR-26) both do the same recompute after changing one job's
  state. `Retry` moves a `failed` job back to `queued` at the back of the
  FIFO (a fresh `queue_seq` from the same Postgres sequence `Create` uses)
  without touching the stored recording -- built and unit-tested now even
  though nothing can produce a `failed` analysis end-to-end until the E3
  worker exists.
- `service/progress` (E5): read-only -- `ListByUser` is the entire service.
  Every write to `progress_snapshots` comes from the E3 worker, never from
  `go-api`, so there is no create/update path to guard here.
- `repository/postgres`: `UserRepository`, `SongRepository` (dedup via
  `GetOrCreate`), `AnalysisRepository` (ownership-scoped `GetByID`/`Cancel`,
  and `RecalculatePositions`, a single `ROW_NUMBER()` query that reassigns
  FIFO position to every queued row -- see ADR-0008 for why position lives
  in Postgres rather than being read back from the Streams entries directly),
  `ProgressRepository` (E5, `ListByUser`, capped at a fixed size -- see
  "Not yet built" for why this isn't cursor-paginated like spec 8.1 asks of
  a browsable list).
- `repository/redisrepo`: refresh-token rotation, login/verification
  throttles, and `AnalysisRateLimiter` (sliding window, `USER_ANALYSES_PER_HOUR`).
- `queue`: Redis Streams producer (`XADD`/`XLEN`/`XDEL`), per ADR-0002.
  `job_id = analysis_id` so redelivery can never duplicate an analysis; the
  worker (below) owns the consumer-group side. Also, since E3,
  `RelayEvents`: a Redis Pub/Sub subscriber (`analyses:events`, ADR-0010)
  forwarding the worker's stage/done/failed events into `transport/ws.Hub`
  -- the worker and `go-api` are separate processes, so this is the only
  channel the worker has to reach a connected browser without polling
  Postgres.
- `media`: magic-byte format detection and ffprobe/ffmpeg wrapping (duration
  probe, canonical-WAV re-encode) -- the spec 11.3 sanitization step, shared
  by both upload and YouTube ingestion.
- `youtube`: yt-dlp metadata/download, host-allowlisted to youtube.com/youtu.be.
- `storage`: audio files under one shared directory (the `audio-tmp` Docker
  volume in compose), named from a server-generated id
  (`song-<id>.wav`/`analysis-<id>.wav`), never a user filename.
- `sysproc`: the `Runner` interface `media`/`youtube` shell out through, so
  their tests don't need ffmpeg/yt-dlp installed.
- `security`, `mailer`, `oauth`: argon2id hashing + HS256 JWTs, SMTP sending,
  Google OAuth2/OIDC with PKCE.
- `transport/http`: chi router; middleware for request ID, panic recovery,
  structured logging, CORS; RFC 9457 `application/problem+json` error
  mapping; DTO validation at the boundary.
- `transport/ws`: the `GET /ws/analyses/{id}` status channel (spec 8.3). The
  client's first message carries the access token (never a query param);
  `Hub.BroadcastPositions` is called by the HTTP handlers after a
  successful `Enqueue`/`Cancel`, the same pattern as auth's handler-level
  cookie-setting after a service call. `Hub.BroadcastStage`/`BroadcastDone`/
  `BroadcastFailed` (E3) are instead called by `queue.RelayEvents`, since
  the events they carry originate in the worker process, not an HTTP
  handler in this one.

## python-worker internal layers (E3)

```
queue/consumer.py  →  queue/handler.py  →  pipeline/runner.py  →  pipeline/stages/*
  (Redis Streams)      (job lifecycle)      (orchestration)        (DSP, one file each)
                                                    ↓
                                          repositories/postgres.py
```

- `worker.py`: the entrypoint, wiring config → Postgres/Redis connections →
  repositories → `ModelRegistry` → the 13 stages → `PipelineRunner` →
  `AnalysisJobHandler` → `Consumer`, then blocking in the consumer loop
  until SIGTERM/SIGINT. `__main__.py` calls
  `runtime.threads.configure_worker_threads()` (M1, spec 6.11) as the very
  first line, before `worker.py` (or anything it imports) ever runs --
  numpy/torch read their BLAS thread pool size from env vars at import
  time, so this has to happen before any of those imports, not inside
  `worker.run()`.
- `pipeline/base.py`/`pipeline/stages/`: `PipelineStage` is the Open/Closed
  seam spec 12.3 asks for -- a new stage is a new class plus one line in
  `worker.build_stages`, nothing else changes. See `docs/ML_PIPELINE.md`
  for what each of the 13 stages does. No stage constructor takes a
  repository: every stage instance is pickled across `PipelineRunner`'s
  spawn-based subprocess boundary, and a `SongRepository` holding a live
  DB connection cannot survive that (a stage that tried this -- `transcribe`
  and `pitch` originally did -- crashed the moment a job actually reached
  it, `TypeError: no default __reduce__ due to non-trivial __cinit__` on
  the connection object). Their song-cache writes (`songs.lyrics_json`,
  `reference_pitch`/`vocal_stem_processed`) move to `AnalysisJobHandler`
  instead, which runs in the parent process and holds the real connection.
  `pipeline/base.py` also defines `ParallelGroup` (M1, spec 6.10): a batch
  of stages with no dependency on each other, run as concurrent
  subprocesses rather than one at a time -- see `docs/ML_PIPELINE.md`'s
  "Parallel aspect stages" section.
- `dsp/`, `runtime/` (M1): DSP primitives every stage that needs them reads
  from, instead of each stage owning its own `librosa`/DTW code --
  `dsp/features.py` (the shared MFCC/RMS/onset cache, spec 6.9),
  `dsp/vad.py` (the pitch-detection VAD gate, spec 6.5), `dsp/dtw.py` (the
  banded two-level DTW, spec 6.7), `runtime/threads.py` (spec 6.11's
  thread configuration).
- `pipeline/runner.py`: `PipelineRunner` orchestrates order, per-stage
  timeout, transient retry, and progress persistence -- and nothing else
  (spec 12.3: "нічого не знає про DSP"). Every stage runs in its own
  spawned child process (ADR-0012), which is what makes a timeout
  enforceable at all and what satisfies spec 6.5's "Demucs and Whisper
  never resident together" as a consequence rather than a special case.
  `ParallelGroup` entries run their members as concurrent subprocesses
  instead, with BLAS threads forced to 1 apiece for the duration
  (spec 6.10).
  Any non-picklable value bound into a stage instance (a lambda closing
  over a local variable, e.g.) fails the same way, only once a job
  actually reaches that stage, not at import/construction time -- see
  `worker.build_stages`'s `functools.partial` comment.
- `pipeline/registry.py`: `ModelRegistry` lazily constructs Demucs/Whisper/
  CREPE/pYIN behind narrow `Protocol`s a stage takes as a constructor
  dependency -- unit tests inject a fake instead of downloading model
  weights (spec 15.2). `DemucsSeparator` never passes Demucs' own `repo=`
  parameter: doing so tells Demucs to treat it as a *local-only* folder
  that must already contain the model files, with no fallback to
  download them, which is not what the `model-weights` volume is (a
  download cache, populated lazily via `TORCH_HOME`/`XDG_CACHE_HOME`).
- `queue/consumer.py`: `XREADGROUP` delivery, `XACK` once a job reaches a
  terminal state, and a startup reclaim sweep over `XPENDING` for a job
  whose worker died mid-stage (spec 10.1) -- mirrors `api/internal/queue`'s
  producer side of the same Redis Stream (`StreamName`/`GroupName` must
  match exactly, ADR-0002). A `NOGROUP` response from Redis (the stream or
  group missing) is caught and recovered -- re-create the group and keep
  going -- rather than left to crash the whole process, which previously
  abandoned every in-flight job until something external restarted the
  container.
- `queue/handler.py`: `AnalysisJobHandler` builds the per-job
  `AnalysisContext` from current DB state, drives the runner, and on
  success denormalizes each aspect stage's score out of `stages_json` into
  its own column (`analyses.pitch_score`, etc.), writes `transcribe`'s/
  `pitch`'s spec 6.6 song cache (`_persist_song_cache`, skipped when a
  stage already served its answer from cache), records a FR-35 progress
  snapshot alongside `save_scoring_result` (E5), then calls `mark_done` --
  `PipelineRunner` itself stays agnostic of which stages happen to produce
  a score or a cache write. Also does the FR-43 cleanup: the scratch work
  dir only once the job is durably `done` (a retryable failure leaves it
  in place -- a retry resumes from stages already in `stages_json` by
  reopening exactly the files `preprocess` wrote there, so deleting it on
  every failure silently broke every retry past `preprocess`), the
  recording only once the job is durably `done` (see
  `service/analysis/retry.go`'s "canonical recording... untouched"
  contract), and the song's original upload once `vocal_stem_processed`
  is true.
- `queue/events.py`: `RedisEventPublisher`, the other end of `go-api`'s
  `queue.RelayEvents` (ADR-0010).
- `repositories/postgres.py`: `PostgresAnalysisRepository`/
  `PostgresSongRepository`, parameterised SQL only, `stages_json` updated
  via a `jsonb ||` merge so one stage's write can never clobber another's.
  `record_progress_snapshot` (E5) upserts on `analysis_id` (a unique
  constraint added alongside it), so a retried job that succeeds updates
  its one chart point instead of the chart gaining a duplicate.

## Song upload / recording / analysis flow (E2 + E3)

1. `POST /songs` (multipart): the audio (uploaded or yt-dlp-downloaded) is
   sniffed by magic bytes, probed for duration, and re-encoded to a
   canonical 16-bit PCM WAV via `ffmpeg` -- this happens for every source,
   uniformly (spec 11.3). Its sha256 (or, for YouTube, the video id) is the
   dedup key; a repeat submission reuses the existing `songs` row instead of
   reprocessing.
2. `POST /analyses` (multipart, JWT-authenticated): the recording goes
   through the identical sniff/probe/transcode pipeline, but only after the
   cheapest checks first -- song exists, per-user rate limit, queue capacity
   (`429 QUEUE_FULL` past `QUEUE_MAX_LENGTH`). On success: a `queued`
   `analyses` row, an `XADD` to the Redis Streams queue, and every queued
   job's position recomputed.
3. `python-worker`'s consumer picks the job up (`XREADGROUP`), runs stages
   1-12 (`docs/ML_PIPELINE.md`), and persists progress after each one.
4. `GET /ws/analyses/{id}` pushes `{"type":"queued","position":N}` while
   queued, then `{"type":"stage",...}` per stage, then `{"type":"done"}` or
   `{"type":"failed",...}` (spec 8.3); `GET /analyses/{id}` is the REST
   fallback/final-result path regardless. The REST resource also carries
   `current_stage_index`/`total_stages`/`current_stage_started_at` (set by
   the same `mark_processing`/`save_stage_progress` writes the WS event
   mirrors) and a `stages` map of each completed stage's real duration, so
   a fresh page load or the polling fallback can render "stage N of M" and
   a live elapsed timer without ever needing the WS message that first
   announced it.
5. `POST /analyses/{id}/cancel` (FR-25) works while `queued`.
   `POST /analyses/{id}/retry` (FR-26) is reachable end-to-end now that E3
   can actually produce a `failed` analysis: it resets status/error/queue
   bookkeeping and re-enqueues, without touching the stored recording or
   `stages_json` -- the worker resumes from the first stage that row
   doesn't already have (spec 6.8).

## Web app (E2)

`web/` is a React 19 + TypeScript (`strict: true`) + Tailwind v4 SPA built
with Vite. One network layer per spec 12.4: `src/api/schema.gen.ts` is
generated from `api/openapi.yaml` (`npm run generate:api`, checked for
drift in CI), consumed through `openapi-fetch`; `src/api/client.ts` wraps
that in the only place the app touches the raw client -- it injects the
bearer token, retries once after a silent `/auth/refresh` on `401`
(concurrent 401s share one in-flight refresh), and turns RFC 9457
`problem+json` bodies into a typed `ApiError`. Server state lives in
TanStack Query; the access token lives in a small module-level store
outside React (`sessionStore.ts`, read via `useSyncExternalStore`) because
the network layer needs synchronous access to it outside any component
tree -- the one exception spec 12.4 allows without an ADR.

`features/analysis/QueueStatus.tsx` follows spec 8.3 exactly: a REST poll
(`useAnalysisStatus`, stopping once the job is terminal) is the source of
truth and fallback transport; `useAnalysisQueueSocket` layers a WebSocket
with capped exponential-backoff reconnect on top purely for low-latency
position pushes -- if it never connects, the poll alone keeps the UI
correct. `features/analysis/useMediaRecorder.ts` wraps `MediaRecorder`
for FR-20 (record/preview/re-record) with an upload fallback for FR-21.

`react-router` is deliberately not a dependency (ADR-0009): every published
7.12+ release has an open high-severity CSRF advisory in its RSC mode
(which this app doesn't use), and pre-7.12 releases carry several older
ones. E2's screen count is small enough that plain component state covers
the whole flow (auth -> add song -> record -> queue); revisit once more
screens need real URL routing.

E5 adds the Progress screen (`features/progress/`) as a second top-level
view, toggled by a `SegmentedControl` nav in `App.tsx` next to the E2-E4
analyze flow -- still no router, since a view toggle is not the same need
as a deep-linkable per-resource URL (ADR-0009's addendum has the current
reasoning).

`features/progress/ProgressChart.tsx` follows the same accessible-chart
pattern PianoRoll set in E4: an SVG/canvas with `role="img"` and a
summarizing `aria-label`, with the actual data available to everyone,
sighted or not, in a plain visible table right below it (`ProgressPage.tsx`)
-- not a screen-reader-only duplicate, since the numbers are useful to look
up regardless of how you're reading the page.

## Why Redis for sessions, not just Postgres

Refresh tokens, the login brute-force throttle, and the verification-resend
throttle all need cheap TTL expiry and instant revocation (log out
everywhere, kill a compromised token family). Postgres can do this but
Redis is the natural fit and is already a required dependency (spec 5.1);
using it for both avoids a second, redundant expiry mechanism.

## Auth flows

**Email registration:** `POST /auth/register` hashes the password, generates
a 6-digit code (hashed with the same argon2id hasher), stores the account
unverified, and emails the code. The response is identical whether or not
the email was already registered (spec 9.1) -- see `service/auth/register.go`.

**Login:** checked against a real or a precomputed dummy password hash so an
unknown email costs the same wall-clock time as a wrong password. A brute
force throttle (per email+IP) imposes exponential backoff and a 15-minute
lockout after 10 failures (spec 9.1).

**Refresh:** opaque token, rotated on every use. Presenting a token that was
already rotated away is treated as a stolen token and revokes every token
descended from that login (spec 9.1's reuse detection).

**Google:** OAuth2 + PKCE + OIDC ID-token verification (`internal/oauth`).
Linking Google to an existing email+password account only happens when
Google itself reports that email as verified. The callback sets the refresh
cookie and redirects to `APP_BASE_URL`; it never puts a token in a URL. The
SPA mints its first access token by calling `/auth/refresh` on load
(`restoreSession` in `web/src/api/client.ts`) -- the email+password path is
wired up in `web/`; the Google button/redirect target is not built yet.

## Deployment boundary

Only `caddy` publishes ports (80/443), and it now serves the built `web/`
SPA directly in addition to proxying `/api/*`/`/healthz`/`/readyz` to
`go-api` (ADR-0013). Postgres, Redis and go-api are reachable solely over
the compose-internal network. Migrations run automatically inside the
`go-api` binary at boot (embedded via `go:embed`, applied with `goose`) --
there is no separate migrate step or container.

## Not yet built

- Google sign-in has no button/redirect target in `web/` yet -- the backend
  flow (`/auth/google`, `/auth/google/callback`) is E1 work with no E2 UI on
  top of it.
- History with pagination (FR-34): there is still no `GET /analyses`
  collection endpoint. `QueueStatus.tsx` only shows the most recent
  analysis it just submitted; the Progress screen's session table (E5) is
  fed by `progress_snapshots` (id, score, date only, no song title), not a
  real history endpoint -- it isn't a substitute for FR-34, just enough to
  make the E5 progress chart's own data legible.

## Audio retention (spec 7.2, FR-43)

The E3 worker (`AnalysisJobHandler._cleanup`) deletes a recording the
moment its analysis reaches `done`, and a song's original upload the
moment its vocal stem is cached (`vocal_stem_processed`) -- both well
inside FR-43's 5-minute bound, since neither waits for a timer.
`storage.FileStore.Sweep` (`main.go`'s `runAudioSweepTicker`,
`orphanedAudioMaxAge = 24h`) is a safety net underneath that, not the
primary mechanism it was in E1/E2: it only catches a file that somehow
never got the worker's precise cleanup (a crash between upload and the
analysis row's insert, or a worker that died before its own cleanup ran
and the job was never retried). The 24-hour age is deliberately far longer
than `AUDIO_TTL_SECONDS` (5 min) specifically so it can never delete a
file a legitimately queued job is still waiting to use -- `QUEUE_MAX_LENGTH`
(20) jobs at spec 6.2's worst-case per-stage timeouts can leave a job
waiting well past 5 minutes before the worker even starts on it.
