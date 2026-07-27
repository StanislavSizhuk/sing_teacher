# Architecture

Status: reflects stages E1-E2. Components and flows planned for later
stages (ML pipeline, frontend) are noted as such, not described as if
they existed.

## Components (target end-state, spec 5.2)

```
                    ┌────────────┐
   Browser ────────►│ Cloudflare │  DNS proxy, DDoS, hides origin IP
                    └─────┬──────┘
                          │ HTTPS
                    ┌─────▼──────┐
                    │   caddy    │  auto-TLS, reverse proxy (+ static React, later)
                    └─────┬──────┘
                          │
                    ┌─────▼──────┐        ┌──────────┐
                    │   go-api   │◄──────►│  redis   │  sessions, throttles, queue
                    │  REST + WS │        └────┬─────┘
                    └─────┬──────┘             │ XADD/XLEN/XDEL
                          │                ┌───▼──────────┐
                    ┌─────▼──────┐         │ python-worker│  ML pipeline (E3)
                    │  postgres  │◄────────┤              │
                    └────────────┘         └──────────────┘
                    ┌───────────┐          ┌────────────────────┐
                    │  backup   │          │ docker volume:     │
                    │ nightly   │          │ audio-tmp (5.2)    │
                    │ pg_dump   │          └────────────────────┘
                    └───────────┘
```

Built in E1: `caddy`, `go-api` (auth), `postgres`, `redis`, `backup`. Built
in E2: song upload/YouTube import, the Redis Streams job queue, and the
WebSocket status channel -- all in `go-api`, since `python-worker` does not
exist yet. `python-worker` itself, and everything in `docs/ML_PIPELINE.md`,
land in E3.

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
  recomputes every queued job's position (spec 10, FR-22). `Cancel` does the
  same recompute after removing a job. Retry (FR-26) is not implemented
  yet -- nothing can produce a `failed` analysis without the E3 worker.
- `repository/postgres`: `UserRepository`, `SongRepository` (dedup via
  `GetOrCreate`), `AnalysisRepository` (ownership-scoped `GetByID`/`Cancel`,
  and `RecalculatePositions`, a single `ROW_NUMBER()` query that reassigns
  FIFO position to every queued row -- see ADR-0008 for why position lives
  in Postgres rather than being read back from the Streams entries directly).
- `repository/redisrepo`: refresh-token rotation, login/verification
  throttles, and `AnalysisRateLimiter` (sliding window, `USER_ANALYSES_PER_HOUR`).
- `queue`: Redis Streams producer (`XADD`/`XLEN`/`XDEL`), per ADR-0002.
  `job_id = analysis_id` so redelivery can never duplicate an analysis; the
  E3 worker owns the consumer-group side.
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
  cookie-setting after a service call.

## Song upload / YouTube import / queue flow (E2)

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
3. `GET /ws/analyses/{id}` pushes `{"type":"queued","position":N}` on every
   change; `GET /analyses/{id}` is the REST fallback/final-result path
   (spec 8.3).
4. `POST /analyses/{id}/cancel` (FR-25) is the only way an analysis leaves
   the queue in this stage -- there is no worker yet to pick jobs up.

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
SPA (once `web/` exists) mints its first access token by calling
`/auth/refresh` on load.

## Deployment boundary

Only `caddy` publishes ports (80/443). Postgres, Redis and go-api are
reachable solely over the compose-internal network. Migrations run
automatically inside the `go-api` binary at boot (embedded via `go:embed`,
applied with `goose`) -- there is no separate migrate step or container.

## Not yet built

- `worker/` (Python ML pipeline) -- E3. Nothing consumes the Redis Streams
  queue yet; jobs stay `queued` until canceled. `songs.vocal_stem_processed`
  stays `false` forever until E3 sets it.
- `web/` (React frontend, including the MediaRecorder-based browser
  recording UI, FR-20) -- E5.
- Retry (FR-26) -- deferred until E3, since nothing can produce a `failed`
  analysis without a worker to fail.
- Everything in `docs/ML_PIPELINE.md` -- created when the pipeline exists.

## Known gap: audio retention without a worker (interim, until E3)

Spec 7.2 ties audio deletion to "5 minutes after processing ends." With no
worker yet, "processing ends" never happens, so `storage.FileStore.Sweep`
(run every minute from `main.go`) instead deletes anything under
`audio-tmp` older than `AUDIO_TTL_SECONDS` from *creation*, regardless of
whether it was ever used. Consequence: a song's canonical audio can be swept
before E3 exists to read it, leaving a `songs` row whose `GetOrCreate` dedup
hit no longer has a backing file. This is expected and harmless at this
stage (nothing reads that file yet); E3's worker should switch the trigger
to actual processing completion when it lands.
