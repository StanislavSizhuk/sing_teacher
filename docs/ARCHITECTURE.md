# Architecture

Status: reflects stage E1. Components and flows planned for later stages
(ML pipeline, job queue, frontend) are noted as such, not described as if
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
                    │   go-api   │◄──────►│  redis   │  sessions, throttles, (later) queue
                    │  REST + WS │        └────┬─────┘
                    └─────┬──────┘             │
                          │                ┌───▼──────────┐
                    ┌─────▼──────┐         │ python-worker│  ML pipeline (E3)
                    │  postgres  │◄────────┤              │
                    └────────────┘         └──────────────┘
                    ┌───────────┐
                    │  backup   │  nightly pg_dump → ./backups
                    └───────────┘
```

Built in E1: `caddy`, `go-api`, `postgres`, `redis`, `backup`. `python-worker`
and the WebSocket status channel do not exist yet -- they land in E2/E3.

## go-api internal layers

```
transport/http  →  service/auth  →  repository/{postgres,redisrepo}
                         ↓
                      domain
```

- `domain`: `User` entity and sentinel errors (`ErrNotFound`,
  `ErrInvalidCredentials`, ...). Knows nothing about HTTP, SQL or Redis.
- `service/auth`: registration, email verification, login, refresh-token
  rotation, Google sign-in, account deletion. Declares every external
  dependency as an interface (`UserRepository`, `RefreshTokenStore`,
  `Mailer`, `PasswordHasher`, `AccessTokenIssuer`, `LoginThrottle`,
  `VerificationThrottle`, `GoogleVerifier`, `Clock`) -- the consumer owns the
  interface, per spec 12.2.
- `repository/postgres`: `UserRepository` over pgx.
- `repository/redisrepo`: refresh-token rotation (with reuse detection and
  family revocation), login brute-force throttle, verification-resend
  throttle. All three live in Redis because all three need instant,
  TTL-based state, not durability.
- `security`, `mailer`, `oauth`: argon2id hashing + HS256 JWTs, SMTP sending,
  Google OAuth2/OIDC with PKCE.
- `transport/http`: chi router; middleware for request ID, panic recovery,
  structured logging, CORS; RFC 9457 `application/problem+json` error
  mapping; DTO validation at the boundary.

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

- `worker/` (Python ML pipeline), Redis Streams job queue, WebSocket status
  channel -- E2/E3.
- `web/` (React frontend) -- E2/E5.
- Everything in `docs/ML_PIPELINE.md` -- created when the pipeline exists.
