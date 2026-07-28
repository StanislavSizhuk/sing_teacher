# AI Vocal Coach

A web app that compares a user's singing to the original vocal of a song and
reports pitch, rhythm, vibrato, breathing, dynamics and timbre. Analysis runs
offline, not in real time.

**Status:** stages E1-E4 (auth, DB schema, song upload/YouTube import, the
analysis job queue with live WebSocket status updates, the Python ML
pipeline -- Demucs separation, Whisper transcription, DTW alignment,
pitch/rhythm/vibrato/dynamics/timbre/breath scoring, weighted score
aggregation and a text report -- and a web UI covering all of that,
including a synced piano-roll). Progress history and the adaptive UI pass
land in stage E5 -- see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/ML_PIPELINE.md](docs/ML_PIPELINE.md).

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
- `python-worker` starts too and picks up queued analyses automatically --
  the first run downloads Demucs/Whisper/CREPE weights into the
  `model-weights-dev` volume, which takes a while; subsequent starts reuse
  the cache.

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

Run the frontend against that dev API (`web/.env.example` already points
`VITE_API_BASE_URL` at `http://localhost:8080/api/v1`, which the dev
compose's `CORS_ALLOWED_ORIGIN` allows):

```bash
cd web
npm install
cp .env.example .env.local
npm run dev           # http://localhost:5173
npm run typecheck && npm run lint && npm run format && npm test && npm run build
```

Run the worker's tests and linters (needs `ffmpeg` on `PATH` and, for
`-m integration`, a real Postgres migrated with the API's `goose`
migrations plus a real Redis -- both already running if the dev compose
stack is up):

```bash
cd worker
uv sync --all-groups
uv run pytest -m "not integration"   # unit tests, synthetic signals only
uv run pytest -m integration         # needs Postgres/Redis, see above
uv run ruff check . && uv run ruff format --check .
uv run mypy .
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
| `api/internal/transport/ws/` | WebSocket status channel (queue position, stage/done/failed) |
| `api/migrations/` | goose SQL migrations, embedded into the binary |
| `api/openapi.yaml` | API contract -- single source of truth |
| `web/` | React + TS + Tailwind SPA: auth, song upload/YouTube, browser recording, analysis queue |
| `web/src/api/` | Generated OpenAPI types, the one typed fetch client, session store |
| `web/src/features/` | `auth`, `songs`, `analysis` -- one directory per feature, not per file type |
| `worker/` | Python: the ML pipeline, consuming the same Redis Streams queue `api/` produces to |
| `worker/src/vocalcoach/pipeline/` | `PipelineStage` base + `ModelRegistry` + one file per stage |
| `worker/src/vocalcoach/pipeline/runner.py` | Orchestration: order, per-stage timeout/retry, progress persistence |
| `worker/src/vocalcoach/queue/` | Redis Streams consumer, job lifecycle, Pub/Sub event publisher |
| `worker/src/vocalcoach/repositories/` | Postgres implementations, same `analyses`/`songs` tables `api/` owns |
| `deploy/` | Compose files, Caddyfile, nightly backup script |
| `docs/` | Architecture, security, ML pipeline, runbook, ADRs |

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) -- components, data flow, boundaries
- [docs/ML_PIPELINE.md](docs/ML_PIPELINE.md) -- stages, parameters, error codes, caching
- [docs/SECURITY.md](docs/SECURITY.md) -- threat model, secrets handling
- [docs/RUNBOOK.md](docs/RUNBOOK.md) -- deploy, rollback, backup restore, incidents
- [docs/adr/](docs/adr/) -- architectural decisions
- [CHANGELOG.md](CHANGELOG.md)

## License / disclaimer

Personal, non-commercial project. YouTube import (when enabled in a later
stage) is for personal use only; see `docs/SECURITY.md` for the associated
ToS/copyright caveat.
