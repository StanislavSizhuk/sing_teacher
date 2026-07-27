# AI Vocal Coach

A web app that compares a user's singing to the original vocal of a song and
reports pitch, rhythm, vibrato, breathing, dynamics and timbre. Analysis runs
offline, not in real time.

**Status:** stages E1-E2 (auth, DB schema, song upload/YouTube import, the
analysis job queue with live WebSocket position updates). The ML pipeline
and frontend land in later stages -- see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

Prerequisites: Docker and Docker Compose.

```bash
git clone <repo-url> ai-vocal-coach && cd ai-vocal-coach
cp .env.example .env
# Fill in JWT_SECRET, POSTGRES_PASSWORD, REDIS_PASSWORD, GOOGLE_CLIENT_ID,
# GOOGLE_CLIENT_SECRET, SMTP_*, and set APP_BASE_URL/CORS_ALLOWED_ORIGIN to
# your real domain.
docker compose -f deploy/docker-compose.yml up -d
```

Migrations apply automatically on boot. Once the stack is healthy:

- `POST https://<your-domain>/api/v1/auth/register`
- `POST https://<your-domain>/api/v1/auth/verify`
- `POST https://<your-domain>/api/v1/auth/login`

See [api/openapi.yaml](api/openapi.yaml) for the full contract.

## Development

The dev stack needs no real secrets to start (mailhog captures verification
emails instead of sending them; Postgres/Redis/JWT get working dev-only
defaults -- see `deploy/docker-compose.dev.yml`):

```bash
cp .env.example .env   # only GOOGLE_CLIENT_ID/SECRET matter if you test Google login
docker compose -f deploy/docker-compose.dev.yml up
```

- API: `http://localhost:8080` (hot-reloads on save via `air`)
- Mailhog UI (captured verification emails): `http://localhost:8025`
- Postgres: `localhost:5432`, Redis: `localhost:6379` (published for a local client)

Run tests and linters from `api/`:

```bash
cd api
go test ./...                    # unit tests
go test -tags=integration ./...  # integration tests (needs Docker, spins up real Postgres/Redis)
gofmt -l .                       # must print nothing
go vet ./...
golangci-lint run                # errcheck, staticcheck, revive, gosec, ineffassign, bodyclose, sqlclosecheck
```

Apply migrations without starting the API (e.g. against a manually-run Postgres):

```bash
cd api
goose -dir migrations postgres "$POSTGRES_DSN" up
```

## Project layout

| Path | Responsibility |
|---|---|
| `api/` | Go: REST + WebSocket API, auth, job queue, DB migrations |
| `api/internal/domain/` | Entities and sentinel errors; no HTTP/SQL/Redis knowledge |
| `api/internal/service/` | Business logic (auth, song ingestion, analysis queue); depends only on interfaces |
| `api/internal/repository/` | Postgres and Redis implementations of those interfaces |
| `api/internal/queue/` | Redis Streams job queue producer |
| `api/internal/media/` | Audio format sniffing, ffprobe/ffmpeg wrapping |
| `api/internal/youtube/` | yt-dlp metadata/download client |
| `api/internal/storage/` | Audio file storage under server-generated paths |
| `api/internal/sysproc/` | External-command runner (DI seam for exec) |
| `api/internal/security/`, `mailer/`, `oauth/` | Password hashing, JWT, SMTP, Google OIDC |
| `api/internal/transport/http/` | chi router, middleware, DTOs, handlers |
| `api/internal/transport/ws/` | WebSocket status channel (analysis queue position) |
| `api/migrations/` | goose SQL migrations, embedded into the binary |
| `api/openapi.yaml` | API contract -- single source of truth |
| `deploy/` | Compose files, Caddyfile, nightly backup script |
| `docs/` | Architecture, security, runbook, ADRs |

`worker/` (Python ML pipeline) does not exist yet; it arrives in stage E3.
`web/` (React frontend, including browser recording) arrives in stage E5.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) -- components, data flow, boundaries
- [docs/SECURITY.md](docs/SECURITY.md) -- threat model, secrets handling
- [docs/RUNBOOK.md](docs/RUNBOOK.md) -- deploy, rollback, backup restore, incidents
- [docs/adr/](docs/adr/) -- architectural decisions
- [CHANGELOG.md](CHANGELOG.md)

## License / disclaimer

Personal, non-commercial project. YouTube import (when enabled in a later
stage) is for personal use only; see `docs/SECURITY.md` for the associated
ToS/copyright caveat.
