# Security

Status: reflects stage E1 (auth + account perimeter). Updated whenever the
perimeter changes (spec 14.1).

## Threat model, in scope for E1

- Credential stuffing / brute force against `/auth/login`.
- Account enumeration via `/auth/register` and `/auth/login` timing or
  response content.
- Refresh-token theft (XSS exfiltration, log leakage, stolen device).
- CSRF against the Google OAuth callback.
- Account takeover via Google sign-in linking to an unverified email.

Out of scope for E1 (nothing to attack yet): file upload handling, YouTube
import, the ML pipeline, the job queue.

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
- CI runs `govulncheck`, `gosec`, and a Trivy image scan on every PR
  (`.github/workflows/ci.yml`); critical/high findings fail the build.

## Not yet applicable

Upload validation (magic bytes, size/duration limits, ffmpeg re-encoding),
YouTube import's ToS/copyright disclaimer requirement, and queue-level rate
limiting all apply starting in E2 and will be documented here when they
land.
