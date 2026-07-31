# Architecture

Status: reflects stages E1-E5, including `web/`, plus the post-E6 fix that
wires `web/` into `docker compose` (ADR-0013), a further post-E6 audit
pass that made the ML pipeline actually complete a real analysis end to
end for the first time (spawn-boundary pickling, Demucs model loading, the
retry/work-dir interaction, a Redis Streams resilience gap) plus the spec
6.9 recording-condition stage, M1's performance pass (shared feature
cache, VAD gate, banded DTW, parallel aspect stages, explicit thread
config), and M2's cold/warm pipeline split (spec 6.2, 10): a second Redis
Stream (`songs:prep`) and worker job kind (`SongPrepJobHandler`) move
Demucs/Whisper/reference-pitch detection out of an analysis's own
critical path into a job that starts the moment a song is added, so the
first analysis of any song is no slower than the second. Components and
flows still planned for later stages (Google sign-in UI, a paginated
analysis history endpoint, `mixed` mode) are noted as such, not described
as if they existed.

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
                    │  REST + WS │        └────┬─────┘  analyses:run +
                    └─────┬──────┘             │        songs:prep (M2),
                          │                    │        event relay
                          │                ┌───▼──────────┐
                    ┌─────▼──────┐         │ python-worker│  cold+warm ML
                    │  postgres  │◄────────┤  (1 replica) │  pipeline (E3, M2)
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
for queue position. Built in M2: a second stream (`songs:prep`) and job
kind (cold path P1-P4) split out of what E3 originally ran inline per
analysis -- see "Song upload / recording / analysis flow" below and
ADR-0024. Built in E4: stage 11 score aggregation and the FR-32
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
  (`ErrNotFound`, `ErrInvalidCredentials`, `ErrQueueFull`,
  `ErrReferencePrepFailed`, `ErrSongPrepNotFailed`, ...). `Song` carries
  `PrepStatus`/`PrepStage`/`PrepErrorCode`/`PreparedAt` (M2) instead of a
  single `VocalStemProcessed` boolean; `Analysis.Status` gained
  `waiting_for_reference`. Knows nothing about HTTP, SQL or Redis.
- `service/auth`: registration, email verification, login, refresh-token
  rotation, Google sign-in, account deletion. Declares every external
  dependency as an interface (`UserRepository`, `RefreshTokenStore`,
  `Mailer`, `PasswordHasher`, `AccessTokenIssuer`, `LoginThrottle`,
  `VerificationThrottle`, `GoogleVerifier`, `Clock`) -- the consumer owns the
  interface, per spec 12.2.
- `service/song`: `AddFromUpload`/`AddFromYouTube` both run
  sniff → probe → transcode → hash → dedup (spec 6.6, FR-10..14), then (M2,
  FR-15) publish the newly-created row's id onto `songs:prep` immediately --
  skipped for a dedup hit, since that song's cold path is already queued,
  running, or done. `RetryPrep` (FR-17) resets a `failed` song back to
  `pending` and republishes it, for `POST /songs/{id}/prepare`.
- `service/analysis`: `Enqueue` validates the recording the same way, then
  branches on the song's `PrepStatus`: `ready` creates the row `queued` and
  publishes it to `analyses:run` exactly as before M2; `pending`/
  `processing` creates it `waiting_for_reference` instead, publishing
  nothing (there is no ML job yet to admit); `failed` is rejected outright
  with `REFERENCE_PREP_FAILED` before a row is even created (spec 6.2,
  10.3, FR-16/17). Every branch still recomputes queued positions the same
  way. `Cancel` and `Retry` (FR-25, FR-26) do the same recompute after
  changing one job's state. `Retry` moves a `failed` job back to `queued`
  at the back of the FIFO (a fresh `queue_seq` from the same Postgres
  sequence `Create` uses) without touching the stored recording.
- `service/progress` (E5): read-only -- `ListByUser` is the entire service.
  Every write to `progress_snapshots` comes from the worker, never from
  `go-api`, so there is no create/update path to guard here.
- `repository/postgres`: `UserRepository`, `SongRepository` (dedup via
  `GetOrCreate`; `RetryPrep` conditional on `prep_status='failed'`),
  `AnalysisRepository` (ownership-scoped `GetByID`/`Cancel`, and
  `RecalculatePositions`, a single `ROW_NUMBER()` query that reassigns FIFO
  position to every queued row -- see ADR-0008 for why position lives in
  Postgres rather than being read back from the Streams entries directly;
  the worker's `wake_waiting_for_reference` mirrors this exact query, see
  ADR-0024), `ProgressRepository` (E5, `ListByUser`, capped at a fixed size
  -- see "Not yet built" for why this isn't cursor-paginated like spec 8.1
  asks of a browsable list).
- `repository/redisrepo`: refresh-token rotation, login/verification
  throttles, and `AnalysisRateLimiter` (sliding window, `USER_ANALYSES_PER_HOUR`).
- `queue`: Redis Streams producer (`XADD`/`XLEN`/`XDEL`), per ADR-0002,
  parameterized by stream/group name (M2) rather than one hardcoded pair --
  `go-api` builds one instance for `analyses:run` (analysis jobs) and one
  for `songs:prep` (cold-path jobs, spec 10.1). `job_id = analysis_id` or
  `song_id` so redelivery can never duplicate a job; the worker (below)
  owns the consumer-group side of both. Also, since E3,
  `RelayEvents`: a Redis Pub/Sub subscriber (`analyses:events`, ADR-0010)
  forwarding the worker's stage/done/failed/queued events into `transport/ws.Hub`
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
  cookie-setting after a service call -- and, since M2, also by
  `queue.RelayEvents` for a `queued` event the worker itself published
  (an analysis woken from `waiting_for_reference`, spec 10.3): one
  `EventSink` method serves both the HTTP-driven and worker-initiated
  path, just called from two different places. `Hub.BroadcastStage`/
  `BroadcastDone`/`BroadcastFailed` (E3) are instead called by
  `queue.RelayEvents`, since the events they carry originate in the
  worker process, not an HTTP handler in this one.

## python-worker internal layers (E3, M2)

```
queue/scheduler.py  →  queue/consumer.py  →  queue/handler.py       →  pipeline/runner.py  →  pipeline/stages/*
  (priority, spec       (Redis Streams,        queue/prep_handler.py    (orchestration,        (DSP, one file each;
   10.2, M2)              per stream)           (job lifecycle,          generic over           P1-P4 cold, A1-A10 warm)
                                                  one per job kind)       ContextT, M2)
                                                                                ↓
                                                                      repositories/postgres.py
```

- `worker.py`: the entrypoint, wiring config → Postgres/Redis connections →
  repositories → `ModelRegistry` → warm-path stages (`build_stages`) and
  cold-path stages (`build_prep_stages`, M2) → two `PipelineRunner`s →
  `AnalysisJobHandler`/`SongPrepJobHandler` → two `Consumer`s (one per
  stream) → `Scheduler`, then blocking in the scheduler loop until
  SIGTERM/SIGINT. `__main__.py` calls
  `runtime.threads.configure_worker_threads()` (M1, spec 6.11) as the very
  first line, before `worker.py` (or anything it imports) ever runs --
  numpy/torch read their BLAS thread pool size from env vars at import
  time, so this has to happen before any of those imports, not inside
  `worker.run()`.
- `pipeline/base.py`/`pipeline/stages/`: `PipelineStage`/`ParallelGroup`
  are generic over `ContextT` (a `PipelineContext` protocol -- `result`/
  `with_result`), so the exact same classes drive both `AnalysisContext`
  (warm path, A1-A10) and `SongPrepContext` (cold path, P1-P4, M2) instead
  of two parallel stage hierarchies (spec 12.1 DRY). `PipelineStage` is
  still the Open/Closed seam spec 12.3 asks for -- a new stage is a new
  class plus one line in `worker.build_stages`/`build_prep_stages`,
  nothing else changes. See `docs/ML_PIPELINE.md` for what each stage
  does. `required: bool` (default `True`, M2) marks an optional stage
  (P3/transcribe): the runner records its failure as `SKIPPED` instead of
  aborting the whole run (spec 6.3, FR-18). No stage constructor takes a
  repository: every stage instance is pickled across `PipelineRunner`'s
  spawn-based subprocess boundary, and a repository holding a live DB
  connection cannot survive that (a stage that tried this -- `transcribe`
  and `pitch` originally did -- crashed the moment a job actually reached
  it, `TypeError: no default __reduce__ due to non-trivial __cinit__` on
  the connection object). `ParallelGroup` (M1, spec 6.10): a batch of
  stages with no dependency on each other, run as concurrent subprocesses
  rather than one at a time -- see `docs/ML_PIPELINE.md`'s "Parallel
  aspect stages" section.
- `dsp/`, `runtime/` (M1, extended M2): DSP primitives every stage that
  needs them reads from, instead of each stage owning its own
  `librosa`/DTW code -- `dsp/features.py` (the shared MFCC/RMS/onset
  cache, spec 6.9), `dsp/vad.py` (the pitch-detection VAD gate, spec 6.5),
  `dsp/dtw.py` (the banded two-level DTW, spec 6.7),
  `dsp/pitch_detection.py` (M2: the VAD-gated detection loop `PitchStage`
  and the cold path's `PrepReferencePitchStage` both call, so the warm and
  cold sides detect pitch identically), `runtime/threads.py` (spec 6.11's
  thread configuration).
- `pipeline/runner.py`: `PipelineRunner` orchestrates order, per-stage
  timeout, transient retry, optional-stage skipping, and progress
  persistence -- and nothing else (spec 12.3: "нічого не знає про DSP").
  Depends on a `ProgressReporter` protocol (`mark_processing`/
  `save_stage_progress`, no job id in either call) rather than a
  repository shaped for one job kind -- `AnalysisProgressReporter`/
  `SongPrepProgressReporter` (in `queue/handler.py`/`queue/prep_handler.py`)
  adapt each job's own id-taking repository methods to it (M2). Every
  stage runs in its own spawned child process (ADR-0012), which is what
  makes a timeout enforceable at all and what satisfies spec 6.5's
  "Demucs and Whisper never resident together" as a consequence rather
  than a special case. `ParallelGroup` entries run their members as
  concurrent subprocesses instead, with BLAS threads forced to 1 apiece
  for the duration (spec 6.10).
- `pipeline/registry.py`: `ModelRegistry` lazily constructs Demucs/Whisper/
  CREPE/pYIN behind narrow `Protocol`s a stage takes as a constructor
  dependency -- unit tests inject a fake instead of downloading model
  weights (spec 15.2). Shared between `build_stages` and `build_prep_stages`
  (M2): the same pitch-engine instance detects both sides of every
  comparison, and the registry's own one-model-at-a-time discipline (spec
  6.5) holds regardless of which stage set loads Demucs/Whisper first.
  `DemucsSeparator` never passes Demucs' own `repo=` parameter: doing so
  tells Demucs to treat it as a *local-only* folder that must already
  contain the model files, with no fallback to download them, which is
  not what the `model-weights` volume is (a download cache, populated
  lazily via `TORCH_HOME`/`XDG_CACHE_HOME`).
- `queue/streams.py` (M2): the two Redis Stream/group name pairs
  (`analyses:run`/`analyses:workers`, `songs:prep`/`songs:prep:workers`),
  shared by `consumer.py`, `prep_handler.py`'s wake-up publisher, and
  their tests -- must match `api/internal/queue`'s constants exactly.
- `queue/consumer.py`: `Consumer`, one instance per stream (M2;
  parameterized by stream/group name and reclaim threshold instead of
  hardcoded, spec 12.1 DRY -- 15 min for `analyses:run`, 20 min for
  `songs:prep`, spec 10.3, since a single P-stage can run far longer than
  any warm-path stage). `XREADGROUP` delivery, `XACK` once a job reaches a
  terminal state, and a startup reclaim sweep over `XPENDING` for a job
  whose worker died mid-stage (spec 10.1) -- mirrors `api/internal/queue`'s
  producer side of the same Redis Streams. A `NOGROUP` response from Redis
  (the stream or group missing) is caught and recovered -- re-create the
  group and keep going -- rather than left to crash the whole process,
  which previously abandoned every in-flight job until something external
  restarted the container.
- `queue/scheduler.py` (M2): `Scheduler` implements spec 10.2's one-ML-slot-
  with-priority rule across both `Consumer`s -- `analyses:run` first when
  it has work; otherwise, if an analysis is `waiting_for_reference`
  (`oldest_waiting_song_id`, mirroring `wake_waiting_for_reference`'s own
  query), that song's `songs:prep` entry jumps the line; otherwise
  `songs:prep` is plain FIFO. Redis Streams has no primitive to selectively
  deliver one arbitrary undelivered entry out of order, so the priority
  jump works by claiming every currently undelivered `songs:prep` entry
  into this consumer's PEL up front (cheap -- the stream is capped at 20,
  spec 10.1), then choosing which *pending* entry to process, which Redis
  does let a caller pick freely. Exactly one job (of either kind) ever
  runs at a time (spec NFR-04/NFR-07) -- the single-threaded loop itself
  is that guarantee, nothing else in the codebase enforces it.
- `queue/handler.py`: `AnalysisJobHandler` builds the per-job
  `AnalysisContext` straight from a song's already-`ready` cold-path
  output (M2: reference decode, Demucs, reference pitch curve all ran
  once, earlier, in the cold path -- this handler never touches Demucs/
  Whisper or the raw reference upload, and raises loudly if handed an
  analysis whose song somehow isn't actually ready, since that is the
  scheduler's invariant to hold, not this handler's to route around),
  drives the runner, and on success denormalizes each aspect stage's score
  out of `stages_json` into its own column (`analyses.pitch_score`, etc.),
  records a FR-35 progress snapshot alongside `save_scoring_result` (E5),
  then calls `mark_done` -- `PipelineRunner` itself stays agnostic of
  which stages happen to produce a score. Also does the FR-43 cleanup: the
  scratch work dir only once the job is durably `done` (a retryable
  failure leaves it in place -- a retry resumes from stages already in
  `stages_json` by reopening exactly the files `preprocess` wrote there),
  the recording only once the job is durably `done` (see
  `service/analysis/retry.go`'s "canonical recording... untouched"
  contract).
- `queue/prep_handler.py` (M2): `SongPrepJobHandler` is `AnalysisJobHandler`'s
  cold-path counterpart -- builds the `SongPrepContext`, drives the same
  generic `PipelineRunner`, and on success writes `vocal_stem_path`/
  `reference_pitch`/`lyrics`/`lyrics_available` (the last two read out of
  whether P3 finished `DONE` or was recorded `SKIPPED`, FR-18) and
  `prep_status='ready'`. Either way (success or failure) it then wakes
  every analysis of that song still `waiting_for_reference`: on success,
  `wake_waiting_for_reference` promotes them to `queued`, an `XADD` per
  newly-queued id onto `analyses:run` (never for one whose position merely
  shifted -- a second stream entry for the same `job_id` would make the
  consumer process it twice), and a `queued` WS event per changed
  position; on failure, `fail_waiting_for_reference` fails them with
  `REFERENCE_PREP_FAILED` (FR-17) instead.
- `queue/events.py`: `RedisEventPublisher`, the other end of `go-api`'s
  `queue.RelayEvents` (ADR-0010); gained `publish_queued` (M2) for the
  worker-initiated wake-up push.
- `repositories/postgres.py`: `PostgresAnalysisRepository`/
  `PostgresSongRepository`, parameterised SQL only, `stages_json`/
  `prep_stages_json` updated via a `jsonb ||` merge so one stage's write
  can never clobber another's. `record_progress_snapshot` (E5) upserts on
  `analysis_id` (a unique constraint added alongside it), so a retried job
  that succeeds updates its one chart point instead of the chart gaining a
  duplicate. `wake_waiting_for_reference`'s position-recalculation query
  (M2) is a deliberate byte-for-byte mirror of
  `AnalysisRepository.RecalculatePositions` on the Go side -- see
  ADR-0024 for why that duplication is the least-bad option here.

## Song upload / recording / analysis flow (E2 + E3, M2)

**Cold path (song added):**

1. `POST /songs` (multipart): the audio (uploaded or yt-dlp-downloaded) is
   sniffed by magic bytes, probed for duration, and re-encoded to a
   canonical 16-bit PCM WAV via `ffmpeg` -- this happens for every source,
   uniformly (spec 11.3). Its sha256 (or, for YouTube, the video id) is the
   dedup key; a repeat submission reuses the existing `songs` row instead of
   reprocessing, and does **not** re-queue its cold path (already queued,
   running, or done). A genuinely new row is published onto `songs:prep`
   in the same request (FR-15), and the response returns immediately with
   `prep_status='pending'` -- the client never waits on Demucs/Whisper.
2. `python-worker`'s scheduler picks the job up (spec 10.2: `analyses:run`
   is checked first, but nothing is competing for the ML slot yet at this
   point for a song nobody has analyzed), runs stages P1-P4
   (`docs/ML_PIPELINE.md`), and persists progress after each one into
   `songs.prep_stage`/`prep_stages_json`. P3 (transcription) is optional
   (FR-18): its failure is recorded as `SKIPPED`, not fatal.
3. On success: `songs.prep_status` flips to `ready`, with the vocal stem
   path, reference pitch curve, and lyrics (if P3 succeeded) all cached on
   the row. Any analysis of this song still `waiting_for_reference` is
   woken (see below). On failure: `prep_status` flips to `failed` with
   `prep_error_code`; `POST /songs/{id}/prepare` (FR-17) restarts it
   without re-uploading, resuming from whichever P-stage's result is
   already in `prep_stages_json` (spec 6.1/6.8).

**Warm path (analysis requested):**

4. `POST /analyses` (multipart, JWT-authenticated): the recording goes
   through the identical sniff/probe/transcode pipeline, but only after the
   cheapest checks first -- song exists and its `prep_status` isn't
   `failed` (`REFERENCE_PREP_FAILED` otherwise, FR-17), per-user rate
   limit. If the song is `ready`: queue capacity (`429 QUEUE_FULL` past
   `QUEUE_MAX_LENGTH`), a `queued` `analyses` row, an `XADD` to
   `analyses:run`, every queued job's position recomputed -- unchanged
   from pre-M2 behavior. If the song is still `pending`/`processing`: a
   `waiting_for_reference` row instead (spec 6.2, 10.3, FR-16), nothing
   published to any stream yet -- there is no ML job to admit until the
   song's cold path finishes.
5. `python-worker`'s scheduler picks the `analyses:run` job up, runs stages
   A1-A10 (`docs/ML_PIPELINE.md`) against the song's already-cached
   reference (never re-decoding it, never re-running Demucs/Whisper), and
   persists progress after each one.
6. `GET /ws/analyses/{id}` pushes `{"type":"queued","position":N}` while
   queued (from the HTTP handler *or*, for a woken analysis, from the
   worker directly once its song reaches `ready`), then
   `{"type":"stage",...}` per stage, then `{"type":"done"}` or
   `{"type":"failed",...}` (spec 8.3); `GET /analyses/{id}` is the REST
   fallback/final-result path regardless. The REST resource also carries
   `current_stage_index`/`total_stages`/`current_stage_started_at` (set by
   the same `mark_processing`/`save_stage_progress` writes the WS event
   mirrors) and a `stages` map of each completed stage's real duration, so
   a fresh page load or the polling fallback can render "stage N of M" and
   a live elapsed timer without ever needing the WS message that first
   announced it.
7. `POST /analyses/{id}/cancel` (FR-25) works while `queued`.
   `POST /analyses/{id}/retry` (FR-26) resets status/error/queue
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

`AnalysisJobHandler._cleanup` deletes a recording the moment its analysis
reaches `done`. `SongPrepJobHandler._cleanup` (M2) deletes a song's
original upload the moment its cold path reaches `ready` -- the warm path
never touches that file at all now, so its lifetime is bounded entirely by
the cold path's own duration, not by how long it takes for someone to
request the song's *first* analysis. Both cases are well inside FR-43's
5-minute bound, since neither waits for a timer.
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
