# Security

Status: reflects stages E1 (auth + account perimeter) and E2 (song/
recording upload, YouTube import, job queue). Updated whenever the
perimeter changes (spec 14.1).

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

Out of scope for E2 (nothing to attack yet): the ML pipeline itself
(`worker/`), since it does not exist until E3.

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
  a generic URL-fetch oracle. A UI disclaimer before first use (spec 11.4)
  is not yet applicable -- there is no UI until `web/` lands (E5).
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
  internet-facing hop and will also serve the static frontend once it exists.
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

- Go module versions are locked via `go.sum`.
- `ffmpeg`/`yt-dlp` are pinned to exact `apk` package versions in the
  production runtime image, not just an unpinned `apk add` (ADR-0007).
- CI runs `govulncheck`, `gosec`, and a Trivy image scan on every PR
  (`.github/workflows/ci.yml`); critical/high findings fail the build.

## Not yet applicable

- The YouTube import UI disclaimer (spec 11.4) -- no UI exists until `web/`
  lands (E5); the feature flag defaults off in production in the meantime.
- Everything in the (not yet built) ML pipeline's own threat surface -- E3.
