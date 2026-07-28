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
