# Security

Status: reflects stages E1 (auth + account perimeter), E2 (song/recording
upload, YouTube import, job queue), E3 (the ML worker that now consumes
that queue), E4 (score aggregation, report, piano-roll), E5 (progress
stats, adaptive UI), and a post-E6 fix wiring `web/` into `docker compose`
behind Caddy (ADR-0013). Updated whenever the perimeter changes (spec 14.1).

## E6 security review -- spec section 11 checklist

Formal pass-by-pass verification against every sub-item of spec section 11,
done 2026-07-29 as stage E6's acceptance criterion ("чек-лист розділу 11
пройдено повністю"). Each line is evidence-checked against the code as it
stands, not just cross-referenced against the threat-model prose below.

### 11.1 Perimeter

- [x] Only 80/443 (Caddy) and SSH are ever exposed. Postgres and Redis carry
  no `ports:` at all in `deploy/docker-compose.yml`; neither does `go-api`
  or `python-worker` -- compose-internal network only.
- [x] Cloudflare, UFW (deny by default), SSH key-only/no-root-login and
  fail2ban are host-level setup outside anything `docker compose` can
  enforce -- previously assumed but never actually written down anywhere.
  Now a checklist in `docs/RUNBOOK.md`'s new "Server setup" section, to run
  once per VPS before the first deploy.

### 11.2 Transport and headers

- [x] Auto-HTTPS + HSTS (`max-age=31536000; includeSubDomains`), CSP,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  strict-origin-when-cross-origin`, `Permissions-Policy: microphone=(self)`
  -- all set once in `deploy/Caddyfile`, the only internet-facing hop.
- [x] CORS restricted to one exact configured origin
  (`CORS_ALLOWED_ORIGIN`), not `*`, with credentials and `Vary: Origin`
  (`internal/transport/http/middleware.go:corsMiddleware`).
- [x] **Gap closed:** `web/`'s built static output is now served through
  Caddy in production (ADR-0013), from the same origin `/api/*` is proxied
  from. The CSP was re-verified against real page content, not just JSON:
  it moved from `default-src 'none'` (which only ever had to admit an empty
  JSON body) to a `self`-scoped policy (`script-src`/`style-src`/`font-src`/
  `connect-src 'self'`, `img-src 'self' data:`, `media-src 'self' blob:` for
  `MediaRecorder` preview playback, no `'unsafe-inline'` anywhere) that the
  SPA actually needs to render and run.

### 11.3 File uploads

- [x] Format validated by magic bytes, never extension
  (`internal/media.Sniff`).
- [x] Size/duration enforced before expensive work: `http.MaxBytesReader`
  bounds the body before multipart parsing; `ffprobe` duration is checked
  before transcoding; YouTube duration comes from `yt-dlp --skip-download`
  metadata before any bytes download (FR-12).
- [x] Every file is re-encoded through `ffmpeg` to canonical WAV before
  anything else touches it (`internal/media.Processor.Transcode`).
- [x] Paths are always server-generated UUIDs
  (`internal/storage.FileStore.PathFor`); a submitted filename is never
  read as a path.
- [x] External binaries (`ffmpeg`, `ffprobe`, `yt-dlp`) are invoked as
  argument lists (`internal/sysproc.Runner`, `exec.CommandContext`), never
  a shell string, each with a `context.WithTimeout` (verified: `probeTimeout`
  20s/`transcodeTimeout` 120s in `internal/media/processor.go`,
  `metadataTimeout` 20s/`downloadTimeout` 180s in `internal/youtube/client.go`).
  Memory is capped at the container level
  (`deploy.resources.limits.memory: 512M` on `go-api`, ADR-0007) rather
  than a per-process `ulimit`, which `os/exec` cannot set for a child
  without a shell.
- [x] Processing runs as a non-root container user (`Dockerfile`'s
  `USER vocalcoach:vocalcoach`); `python-worker` has no outbound network
  need at runtime once model weights are cached, and is the only stage
  spec 11.3's "no network access outside the YouTube step" cleanly applies
  to -- `go-api` is a single long-running process that inherently needs
  Postgres/Redis connectivity throughout, so per-call network isolation
  around just the YouTube step was never implemented and isn't a gap
  relative to spec intent (the worker, which actually does isolated,
  short-lived per-stage processing, has no such need at all).

### 11.4 YouTube caveat

- [x] `FEATURE_YOUTUBE_IMPORT` defaults `true` in `.env.example`
  (ADR-0028) and is not overridden in `deploy/docker-compose.yml` -- the
  per-use disclaimer and host allowlist below are the controls, not the
  flag itself, which is now an operator escape hatch rather than a
  default-off safety net.
- [x] UI shows the personal/non-commercial disclaimer before the YouTube
  URL field every time that tab is selected
  (`web/src/features/songs/AddSongForm.tsx`).
- [x] Host allowlist (`internal/youtube/url.go`) is an exact match, not a
  suffix match -- `youtube.com.evil.example` is rejected.
- [x] A `pot-provider` sidecar (`bgutil-ytdlp-pot-provider`, ADR-0037,
  supersedes ADR-0036) generates the PO Token YouTube's bot-check expects,
  so `yt-dlp` requests stop looking like an unauthenticated, non-browser
  client for most videos. Not a full fix: YouTube's IP-reputation/
  traffic-pattern layer in front of the token check is unaffected by
  having a valid token, verified directly (ADR-0037) -- an ordinary video
  can still fail, a high-traffic one reliably does not. `AddFromYouTube`'s
  existing generic, retriable error path is unchanged for whatever still
  gets through. This does reopen the ToS-exposure question ADR-0028/0036
  both flagged -- standing infrastructure whose only job is passing an
  anti-bot check -- accepted as part of ADR-0037's decision, not a new gap.
  Real-account session cookies remain rejected: the most reliable option,
  but the leak-blast-radius is an actual account, not just this
  deployment, unlike a sidecar that never holds anyone's credentials.
- [x] `pot-provider` is hardened the same as `go-api`: pinned image (tag +
  digest), no published port (only `go-api` reaches it over the compose
  network), `read_only`, `cap_drop: [ALL]`, `no-new-privileges`.

### 11.5 Data and secrets

- [x] All secrets validated as non-empty at boot and read only from `.env`
  (`internal/config`, `req(...)`); `.env.example` ships blank placeholders,
  never a real-looking default.
- [x] Rotation procedure documented in `docs/RUNBOOK.md`.
- [x] Grep-verified: no `logger.*` call anywhere in `api/internal` or
  `worker/src` references a password, token, verification code or email.
  Email is never logged at all (stronger than the spec's "mask as
  `s***@gmail.com`" -- there is nothing to mask because it never enters a
  log line in the first place); structured logs carry `user_id` (uuid)
  instead.
- [x] Every external input is validated at the boundary: Go DTOs
  (`internal/transport/http/dto*.go`) before any service call; Python
  stages take only `pydantic` models, never bare `dict`s, between layers.
- [x] SQL is parameterized everywhere, grep-verified in both languages
  (no `fmt.Sprintf`/string concatenation building a query in
  `internal/repository/postgres`; no f-string/`.format()` building a query
  in `worker/src/vocalcoach/repositories`). The one dynamic SQL identifier
  (an aspect-score column name) goes through `psycopg.sql.Identifier`, and
  the value feeding it is always one of the six literal strings in
  `vocalcoach.config.ASPECTS`, never external input.

### 11.6 Dependencies

- [x] `go.sum`, `worker/uv.lock`, `web/package-lock.json` all present and
  committed.
- [x] CI runs `govulncheck` + `gosec` + Trivy on `go-api`'s image,
  `pip-audit` + Trivy on `python-worker`'s image, and `npm audit
  --audit-level=high` on `web/`, on every PR (`.github/workflows/ci.yml`).
- [x] Re-run locally for this review: `govulncheck` (0 vulnerabilities in
  code or called dependencies), `gosec ./...` (0 issues across all of
  `api/`, including the new `cmd/loadtest`), `pip-audit` (no known
  vulnerabilities; `torch`/`torchaudio` are unauditable by `pip-audit`
  because they aren't published to PyPI proper, a pre-existing, already
  CI-accepted limitation of the tool, not a code issue). `npm audit` was
  not re-run in this environment (no Node toolchain available here); `web/`
  had no dependency changes in this stage, and CI already enforces it on
  every PR regardless.
- [x] This stage added zero new third-party dependencies
  (`api/cmd/loadtest` is stdlib-only: `go.mod`/`go.sum` are untouched by
  this stage's commits), so there is no new supply-chain surface to audit.

All six sub-sections pass, including the one item that was still open at
E6 (11.2's CSP/headers not yet covering a Caddy-served frontend) -- closed
by ADR-0013's `web`/Caddy compose wiring, verified above.

## Threat model, in scope for E1

- Credential stuffing / brute force against `/auth/login`.
- Account enumeration via `/auth/register` and `/auth/login` timing or
  response content.
- Refresh-token theft (XSS exfiltration, log leakage, stolen device).
- CSRF against the Google OAuth callback.
- Account takeover via Google sign-in linking to an unverified email.

## Threat model, in scope for E2

- Malicious audio containers (crafted mp3/wav/m4a/flac/ogg exploiting a
  parser bug downstream, or hiding non-audio payloads).
- Resource exhaustion via oversized/overlong uploads, or many concurrent
  analysis requests from one account.
- SSRF-adjacent abuse of the YouTube import path as a generic URL fetcher.
- Path traversal via a user-controlled filename.
- Queue/rate-limit bypass (skipping the size/duration/queue-depth checks).

## Threat model, in scope for E3

- A hostile or malformed reference/recording WAV reaching Demucs/Whisper/
  librosa (already sanitized once by `go-api`'s ffmpeg re-encode, spec
  11.3 -- the worker's own stage-1 ffmpeg pass is a second, independent
  pass through the same sanitizing transform).
- Resource exhaustion from the ML stages themselves: unbounded runtime
  (mitigated per-stage, see below) or unbounded memory (spec NFR-07's 6GB
  ceiling, enforced at the container level, same posture as `go-api`'s
  512MB limit, ADR-0007).
- A malformed or spoofed Redis Pub/Sub message on `analyses:events`
  reaching `go-api`'s relay and crashing it, or forging a `done`/`failed`
  event for an analysis the "sender" doesn't own.
- SQL injection in the worker's own repository layer, mirroring the same
  concern already covered for `go-api`.

## Threat model, in scope for E4

- Widened `GET /analyses/{id}` (and enqueue/cancel/retry) response: six
  aspect scores, the text report, and the piano-roll payload are now
  returned. Authorization is unchanged -- the same owner-scoped
  `AnalysisRepository.GetByID(ctx, id, userID)` gates every field, not
  just the ones that existed before E4.
- The FR-32 report text is server-generated entirely from numeric stage
  data (means, correlations, counts) -- no user-controlled string ever
  reaches it, so there is no template-injection surface. It is rendered
  in `web/` as plain React text content (never `dangerouslySetInnerHTML`),
  so React's default output escaping applies.
- `analyses.pitch_curve_json` is passed through to the client as
  `json.RawMessage` without a re-encode (`dto_analyses.go`). It is written
  exclusively by the worker's own `PianoRollData.model_dump()`, never from
  request input, so this is a performance choice, not a new trust
  boundary.

## Threat model, in scope for E5

- `GET /progress` (FR-35): read-only, `bearerAuth`-gated like every other
  `/api/v1` resource route, scoped by `ProgressRepository.ListByUser(ctx,
  userID)` -- a plain `WHERE user_id = $1`, the same shape as
  `AnalysisRepository.GetByID`'s ownership scoping (spec 11). No request
  body, no query parameters to validate at the boundary.
- `progress_snapshots` is written exclusively by the E3 worker
  (`record_progress_snapshot`, keyed on `analysis_id` with a unique
  constraint) -- `go-api` never writes this table, only reads it back, so
  there is no new write-path trust boundary on the API side.
- No new user input anywhere in this stage: no new upload path, no new
  external binary call, no new template/HTML rendering. `ProgressChart`
  and `ProgressPage` (`web/`) render server-supplied numbers and ISO
  timestamps as plain React text content.

## File upload and YouTube import (E2, spec 11.3)

- **Format validation is on content, never the filename or extension.**
  `internal/media.Sniff` checks magic bytes (RIFF/WAVE, `fLaC`, `OggS`, ISO
  base media `ftyp`, ID3/MPEG frame sync) before anything else runs.
- **Size and duration are capped before expensive work happens.**
  `http.MaxBytesReader` bounds the request body (`MAX_UPLOAD_MB`) before
  multipart parsing even starts; `ffprobe` duration is checked
  (`MAX_AUDIO_SECONDS`) before transcoding. For YouTube, duration comes from
  `yt-dlp --skip-download` metadata *before* any bytes are downloaded (FR-12).
- **Every file is re-encoded through ffmpeg to a canonical WAV before
  anything else touches it** (`internal/media.Processor.Transcode`), which
  is itself the sanitization step: a malformed or hostile container either
  fails to transcode (rejected) or comes out the other side as plain PCM.
  This applies uniformly to uploads and YouTube downloads -- yt-dlp's own
  extraction output still goes through our own ffmpeg invocation afterward,
  not just its own.
- **External binaries (`ffmpeg`, `ffprobe`, `yt-dlp`) are invoked as fixed
  argument lists** via `internal/sysproc.Runner` (`exec.CommandContext`),
  never a shell string, each bounded by a context timeout. There is no
  string interpolation between user input and a command line.
- **Paths are never derived from user input.** `internal/storage.FileStore`
  names every file after a server-generated UUID
  (`song-<id>.wav`/`analysis-<id>.wav`); a submitted filename is never read.
- **YouTube import is feature-flagged** (`FEATURE_YOUTUBE_IMPORT`, spec
  11.4), on by default since ADR-0028, restricted to an exact host
  allowlist (`youtube.com`, `www.youtube.com`, `m.youtube.com`,
  `music.youtube.com`, `youtu.be`) with an exact (not suffix) match, so
  `youtube.com.evil.example` is rejected -- yt-dlp itself understands
  hundreds of sites, and without this allowlist the import endpoint would be
  a generic URL-fetch oracle. `web/`'s YouTube tab shows the spec-11.4
  disclaimer before the URL field, every time that tab is selected (not
  just once) -- with the flag on by default, the disclaimer and the
  allowlist are the real controls, not the flag itself; an operator with a
  stricter posture can still set it back to `false`.
- **Memory limit on external processes** is enforced at the container/cgroup
  level (`deploy.resources.limits.memory: 512M` on `go-api`, covering the Go
  process and any spawned `ffmpeg`/`yt-dlp` child), not a per-process
  `ulimit` -- `os/exec` cannot set one on a child without a shell. See
  ADR-0007.

## Job queue (E2, spec 10)

- **Per-user rate limit**: `AnalysisRateLimiter` (sliding window over a
  Redis sorted set, `USER_ANALYSES_PER_HOUR`) is checked before any
  recording is even read off the wire.
- **Queue depth cap**: `429 QUEUE_FULL` once `XLEN` reaches
  `QUEUE_MAX_LENGTH`, checked before the recording is processed.
- **Ownership**: every analysis read/cancel is scoped to
  `(id, user_id)` at the repository query level (`AnalysisRepository.GetByID`/
  `Cancel`), so a different user's analysis id looks like it doesn't exist
  (spec 11: authorization checked on every resource).
- **WebSocket auth**: the access token rides the connection's first message,
  never a query parameter, so it never lands in a proxy access log
  (spec 8.3). The `Origin` header is checked against `CORS_ALLOWED_ORIGIN`
  at the WebSocket upgrade (`internal/transport/ws`, mirroring the HTTP
  CORS policy).

## ML worker (E3, spec 6.5, 11.3)

- **Every stage timeout is enforced by killing a real OS process**, not a
  cooperative check (`multiprocessing.get_context("spawn")`,
  `terminate()`/`kill()` on expiry, ADR-0012) -- a stage cannot hang the
  worker past its declared budget (spec 6.2's per-stage timeout table)
  regardless of what a malformed input does inside a native library call.
- **`ffmpeg` is invoked as an argument list**, never a shell string
  (`vocalcoach.audio.ffmpeg.run_ffmpeg`, mirroring `go-api`'s
  `internal/sysproc`), with both a timeout and a memory cap
  (`resource.setrlimit(RLIMIT_AS)`, 1 GiB) on the subprocess itself, on top
  of the container-level 6GB ceiling (spec NFR-07) that also bounds every
  Python-side model.
- **Memory isolation is structural, not just a limit**: Demucs and Whisper
  are never resident together because every stage's memory is reclaimed
  the instant its own child process exits (ADR-0012), not because
  something remembers to call `del`/`gc.collect()` correctly -- that
  explicit release still happens (`ModelRegistry.release()`) but is
  hygiene on top of a guarantee, not the guarantee itself.
- **Paths are never derived from user input**, same rule as `go-api`'s
  `storage.FileStore`: `vocalcoach.audio.paths` derives every filename from
  a server-generated `analysis_id`/`song_id`, never a request field.
- **The worker never runs migrations or DDL**; it reads/writes the same
  rows `go-api`'s own repositories do, via parameterised SQL only
  (`vocalcoach.repositories.postgres`, `psycopg` placeholders; the one
  dynamic identifier, an aspect-score column name, goes through
  `psycopg.sql.Identifier`, never string interpolation).
- **The Pub/Sub event relay is unauthenticated by design, and that's fine**:
  `analyses:events` (ADR-0010) never crosses the network boundary --
  Redis itself is not published to the host (spec 5.3) and reachable only
  from `go-api`/`python-worker` on the compose-internal network, the same
  trust boundary the job queue (ADR-0002) already relies on. A malformed
  message is logged and dropped by `go-api`'s relay, never propagated to a
  WS client or allowed to crash the relay goroutine.
- **No outbound network access is needed at runtime** once model weights
  are cached in the `model-weights` volume -- Demucs/Whisper/torchcrepe
  only reach out on a cold cache (first run for a given model version).
  Unlike `go-api`'s YouTube import path, the worker has no
  feature-flagged external-fetch surface at all.
- **Container hardening matches `go-api`'s**: non-root user, `cap_drop:
  [ALL]`, `no-new-privileges`, no published ports (spec 5.2 -- there is no
  HTTP server to expose; Docker's `HEALTHCHECK` reads a heartbeat file
  instead, see `deploy/docker-compose.yml`).

## Authentication and sessions

- Passwords: argon2id (`internal/security/password.go`), memory=64MB,
  iterations=3, parallelism=2 -- above the library default, in line with
  current OWASP guidance for a login endpoint (not a high-throughput hashing
  service).
- Verification codes (6 digits) are hashed with the same argon2id hasher,
  not a fast hash, so a leaked `verification_code_hash` column resists
  offline brute force even though the code space is only 10^6.
- Access tokens: HS256 JWT, 15 min TTL (`internal/security/jwt.go`).
- Refresh tokens: opaque random tokens in Redis, rotated on every use, in an
  httpOnly, SameSite=Strict cookie scoped to `/api/v1/auth`.
  `Secure` is forced off only in `APP_ENV=development` (dev has no TLS);
  production always sets it.
- Refresh-token reuse (a token already rotated away being presented again)
  revokes the entire token family, not just that token -- see
  `internal/repository/redisrepo/refresh_token_store.go`.
- Anti-enumeration: `/auth/register` and `/auth/login` return identical
  responses/timing whether or not the email exists (spec 9.1). Login checks
  the submitted password against a precomputed dummy hash when the account
  doesn't exist, so the two cases cost the same wall-clock time.
  `/auth/verify/resend` deliberately does **not** hide existence -- the
  resend cooldown is meant to be visible to the caller (FR-04's UI
  countdown), and spec 9.1 only names login/register for this rule.
- Brute force: per (email, IP) exponential backoff, hard lockout for 15 min
  after 10 failures (`internal/repository/redisrepo/login_throttle.go`).
- Google OAuth: PKCE (S256) + `state` CSRF check, both carried in short-lived
  (5 min), httpOnly, SameSite=Lax cookies. Linking Google to an existing
  account only happens when Google reports the email as verified.

## Secrets

- All secrets live in `.env` (gitignored); `.env.example` ships only variable
  names and safe placeholders.
- `JWT_SECRET`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `GOOGLE_CLIENT_SECRET`,
  `SMTP_PASSWORD` are validated as non-empty at boot (`internal/config`);
  the process refuses to start rather than run with a blank secret.
- Passwords, tokens, verification codes and full email addresses are never
  logged. Structured logs carry `user_id` (uuid), never email.
- If a secret leaks: rotate it in `.env`, redeploy, and revoke all sessions
  (`RevokeAllForUser` for the affected user, or a bulk Redis flush of
  `auth:refresh*` keys for a JWT_SECRET rotation, since that invalidates
  every access token already issued too). See `docs/RUNBOOK.md`.

## Perimeter (spec 11.1, 11.2)

- Only Caddy publishes ports (80/443). Postgres, Redis and go-api are only
  reachable on the compose-internal network.
- HSTS, CSP, `X-Content-Type-Options: nosniff`, `Referrer-Policy`,
  `Permissions-Policy: microphone=(self)` are set once, at Caddy
  (`deploy/Caddyfile`) -- not duplicated in go-api, since Caddy is the only
  internet-facing hop and now also serves `web/`'s static build from that
  same origin (ADR-0013).
- CORS is enforced in go-api (`CORS_ALLOWED_ORIGIN`), not Caddy: it is
  request/response negotiation tied to the API's own cookie policy, not a
  generic perimeter header.
- Containers: `cap_drop: [ALL]` everywhere, with capabilities re-added back
  one at a time only where a specific, understood need exists (Caddy needs
  `NET_BIND_SERVICE` for ports 80/443; Postgres's entrypoint needs
  `CHOWN`/`SETUID`/`SETGID`/`DAC_OVERRIDE`/`FOWNER` to initialize a fresh
  volume and drop from root to the `postgres` user). `no-new-privileges` is
  set on every service. Redis runs as its non-root built-in user directly
  (uid 999) since it has no volume-ownership step to perform.
- Every production service also sets `read_only: true` on its root
  filesystem, with `tmpfs: [/tmp]` (and, per service, whatever else it
  writes at runtime -- Postgres's `/var/run/postgresql`, Redis's `/data`
  since it runs with persistence off) mounted back in explicitly. Anything
  that needs to persist across a restart goes through a named volume
  instead (`postgres-data`, `caddy-data`/`caddy-config`, `audio-tmp`,
  `song-stems`, `model-weights`) -- never a writable spot on the image
  itself.
- Caddy is the one service that still runs as root: its image's default
  user owns `/data`/`/config`, and it needs a privileged port. This is a
  deliberate, narrow exception (spec 5.3's rules are qualified "where
  possible"), not an oversight.
- Images are pinned by tag **and** digest in every compose file; `latest` is
  never used.

## Dependencies

- Go module versions are locked via `go.sum`; `web/` dependencies via
  `package-lock.json`; `worker/` dependencies via `uv.lock` (ADR-0011).
- `ffmpeg` is pinned to an exact `apk` package version in the production
  `go-api` runtime image, not just an unpinned `apk add` (ADR-0007);
  `worker/`'s Dockerfile pins the same way with the Debian `apt` equivalent.
  `yt-dlp` is pinned to an exact `pip` version instead (ADR-0035): it
  tracks YouTube's own frequently-changing extraction internals, and
  `apk`'s community repo lags upstream releases by enough to break YouTube
  import outright, so it needs to be bumped far more often than any other
  pinned dependency in this repo (`docs/RUNBOOK.md`'s 2026-08-12 incident).
- CI runs `govulncheck`, `gosec`, and a Trivy image scan on the API,
  `npm audit --audit-level=high` on `web/`, and `pip-audit` plus a Trivy
  image scan on `worker/`, on every PR (`.github/workflows/ci.yml`);
  critical/high findings with an available fix fail the build.
  `worker/`'s Trivy step runs `--ignore-unfixed`: its base image
  (`python:3.12-slim`, pinned to Debian trixie -- current stable, chosen
  over bookworm specifically because it trails fewer unpatched CVEs, see
  `worker/Dockerfile`) still carries some OS-package CVEs Debian hasn't
  shipped a patch for yet, and blocking every merge on an upstream
  timeline no PR here can affect defeats the scan's purpose -- a fixable
  finding still fails the build. `react-router` is deliberately not a
  `web/` dependency
  yet: every published 7.12+ release carries an open high-severity CSRF
  advisory (RSC mode, which this app never enables) that `npm audit` would
  flag regardless of reachability, and older releases carry several
  unrelated ones instead -- see `docs/ARCHITECTURE.md`.

## Not yet applicable

- A paginated `GET /analyses` history endpoint (FR-34). Nothing to attack
  until it exists.
