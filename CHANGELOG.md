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
