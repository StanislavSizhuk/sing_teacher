# AI Vocal Coach

An AI vocal coach: sing along to a song, and it scores your pitch, rhythm,
vibrato, breathing, dynamics and timbre against the original vocal.

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

Migrations apply automatically on boot. Once the stack is healthy, open
`https://<your-domain>/` for the web UI -- Caddy serves the built SPA and
proxies `/api/v1/*` to `go-api` from that same origin (ADR-0013):

- `POST https://<your-domain>/api/v1/auth/register`
- `POST https://<your-domain>/api/v1/auth/verify`
- `POST https://<your-domain>/api/v1/auth/login`

See [api/openapi.yaml](api/openapi.yaml) for the full contract.

## Development

The dev stack needs no real secrets to start (mailhog captures verification
emails instead of sending them; Postgres/Redis/JWT/Google all get working
dev-only defaults -- see `deploy/docker-compose.dev.yml`):

```bash
cp .env.example .env   # real GOOGLE_CLIENT_ID/SECRET only needed to exercise Google login itself
docker compose -f deploy/docker-compose.dev.yml up
```

- Web: `http://localhost:5173` (hot-reloads on save via Vite)
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

The dev compose stack already serves `web/` itself (`VITE_API_BASE_URL`
points at `http://localhost:8080/api/v1`, which the dev compose's
`CORS_ALLOWED_ORIGIN` allows), so no separate `npm run dev` is needed. Run
its tests and linters directly with Node (not through Docker):

```bash
cd web
npm install
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
| `api/cmd/loadtest/` | Local load-test CLI (spec 18/E6), see `docs/LOAD_TESTING.md` |
| `web/` | React + TS + Tailwind SPA: auth, song upload/YouTube, browser recording, analysis queue |
| `web/src/api/` | Generated OpenAPI types, the one typed fetch client, session store |
| `web/src/features/` | `auth`, `songs`, `analysis` -- one directory per feature, not per file type |
| `worker/` | Python: the ML pipeline, consuming the same Redis Streams queue `api/` produces to |
| `worker/src/vocalcoach/pipeline/` | `PipelineStage` base + `ModelRegistry` + one file per stage |
| `worker/src/vocalcoach/pipeline/runner.py` | Orchestration: order, per-stage timeout/retry, progress persistence |
| `worker/src/vocalcoach/queue/` | Redis Streams consumer, job lifecycle, Pub/Sub event publisher |
| `worker/src/vocalcoach/repositories/` | Postgres implementations, same `analyses`/`songs` tables `api/` owns |
| `deploy/` | Compose files, Caddyfile, nightly backup script, `deploy.sh` (deploy with automatic rollback) |
| `docs/` | Architecture, security, ML pipeline, runbook, ADRs |

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) -- components, data flow, boundaries
- [docs/ML_PIPELINE.md](docs/ML_PIPELINE.md) -- stages, parameters, error codes, caching
- [docs/SECURITY.md](docs/SECURITY.md) -- threat model, secrets handling
- [docs/RUNBOOK.md](docs/RUNBOOK.md) -- deploy, rollback, backup restore, incidents
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) -- measured stage timings against the spec 6.17 budget
- [docs/LOAD_TESTING.md](docs/LOAD_TESTING.md) -- run `api/cmd/loadtest` locally against the dev stack
- [docs/adr/](docs/adr/) -- architectural decisions
- [CHANGELOG.md](CHANGELOG.md)

## License / disclaimer

Personal, non-commercial project. YouTube import is for personal use
only; see `docs/SECURITY.md` for the associated ToS/copyright caveat.
