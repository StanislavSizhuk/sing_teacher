# Security

Status: reflects stages E1 (auth + account perimeter), E2 (song/recording
upload, YouTube import, job queue) and E3 (the ML worker that now consumes
that queue). Updated whenever the perimeter changes (spec 14.1).

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
- **YouTube import is feature-flagged off by default in production**
  (`FEATURE_YOUTUBE_IMPORT`, spec 11.4) and, when enabled, restricted to an
  exact host allowlist (`youtube.com`, `www.youtube.com`, `m.youtube.com`,
  `music.youtube.com`, `youtu.be`) with an exact (not suffix) match, so
  `youtube.com.evil.example` is rejected -- yt-dlp itself understands
  hundreds of sites, and without this allowlist the import endpoint would be
  a generic URL-fetch oracle. `web/`'s YouTube tab shows the spec-11.4
  disclaimer before the URL field, every time that tab is selected (not
  just once) -- the feature flag defaulting off in production is the real
  control; the disclaimer is belt-and-suspenders for when it's on.
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
  internet-facing hop and will also serve `web/`'s static build once that
  wiring lands (see `docs/ARCHITECTURE.md`'s "Not yet built"). In the
  meantime `web/` runs as its own Vite dev server with no production
  deployment path yet, so these headers don't apply to it in practice.
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
- Caddy is the one service that still runs as root: its image's default
  user owns `/data`/`/config`, and it needs a privileged port. This is a
  deliberate, narrow exception (spec 5.3's rules are qualified "where
  possible"), not an oversight.
- Images are pinned by tag **and** digest in every compose file; `latest` is
  never used.

## Dependencies

- Go module versions are locked via `go.sum`; `web/` dependencies via
  `package-lock.json`; `worker/` dependencies via `uv.lock` (ADR-0011).
- `ffmpeg`/`yt-dlp` are pinned to exact `apk` package versions in the
  production `go-api` runtime image, not just an unpinned `apk add`
  (ADR-0007); `worker/`'s Dockerfile pins the same way with the Debian
  `apt` equivalent.
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

- The score/report display surface (E4) -- there is no report to attack
  until stage 11 and the piano-roll UI exist.
