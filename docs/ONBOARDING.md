# Onboarding

Getting productive on this project from zero, in about a day. Updated once
per stage (spec 14.1) -- this revision covers E1-E4.

## 1. Read, in order (30-45 min)

1. `TZ_AI_Vocal_Coach_v1.0.md` (`tech.md`) sections 1-2 -- what this product
   is and, just as important, what it deliberately is not (2.2).
2. `CLAUDE.md` -- the condensed working rules; this is what a PR is reviewed
   against day to day.
3. `docs/ARCHITECTURE.md` -- current components and layering.
4. `docs/adr/` -- every accepted decision, in order. Each is one page.

## 2. Get the stack running (15 min)

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.dev.yml up
```

No real secrets needed except `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` (only
if you're testing Google login specifically). Verification emails land in
mailhog at `http://localhost:8025`, not a real inbox.

Try the golden path with curl: register → read the code from mailhog →
verify → login → add a song (`POST /songs`, multipart) → submit a recording
(`POST /analyses`, multipart) → watch it move from `queued` through each
pipeline stage to `done`/`failed` over `GET /ws/analyses/{id}`
(`GET /analyses/{id}` if you'd rather poll). `api/openapi.yaml` has the
exact request/response shape for every endpoint. The first analysis of a
new song is slow (Demucs + Whisper run cold, and the first-ever run also
downloads their weights into `model-weights-dev`); a second analysis of
the same song is much faster (spec 6.6's cache).

Or drive the same flow from the browser:

```bash
cd web && npm install && cp .env.example .env.local && npm run dev
```

`http://localhost:5173` -- register, verify, log in, add a song, record (or
upload) a take, watch the queue screen.

## 3. Where things live

See the table in `README.md`. The one rule worth internalizing before
writing any Go: dependencies point inward --
`transport → service → repository → domain` -- and interfaces are declared
by the consumer (`service`), implemented by the infrastructure package
(`repository`, `security`, `mailer`, `oauth`). If you're writing an interface
next to its implementation instead of next to its caller, it's in the wrong
place.

The same rule applies in `worker/`, via `typing.Protocol` instead of a Go
interface: `PipelineRunner` depends on `RunnerAnalysisRepository` (just
`mark_processing`/`save_stage_progress`), not the full `AnalysisRepository`
`repositories/postgres.py` implements -- each consumer's own file declares
exactly the narrow slice it calls. If you're adding a call from, say,
`AnalysisJobHandler` to a repository method it didn't need before, widen
`HandlerAnalysisRepository` in `queue/handler.py`, not the shared interface
in `repositories/interfaces.py`.

## 4. Before your first PR

- `cd api && go test ./... && go test -tags=integration ./... && gofmt -l . && go vet ./... && golangci-lint run`
- `cd web && npm run typecheck && npm run lint && npm run format && npm test && npm run build`
- `cd worker && uv run pytest -m "not integration" && uv run pytest -m integration && uv run ruff check . && uv run ruff format --check . && uv run mypy .`
- Read `docs/REVIEW_CHECKLIST.md` -- that's what gets checked.
- Commit format is enforced by `.githooks/commit-msg`
  (`git config core.hooksPath .githooks` after cloning -- do this before your
  first commit). See `CLAUDE.md` for the exact format.
- One PR = one `FR-*` requirement, diff under ~400 lines.

## 5. What doesn't exist yet

A paginated history endpoint (`GET /analyses`, FR-34) is still not built --
the E5 Progress screen's session table is fed by `progress_snapshots`
(score and date only, no song title), not a real history list. `web/`
isn't wired into Caddy/compose for production either -- it runs as its own
dev server; that's deploy/CD work (E6). Google sign-in has a working
backend flow but no button/redirect target in `web/`. Check `tech.md`
section 18 for what each stage adds.
