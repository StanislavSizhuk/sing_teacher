# Onboarding

Getting productive on this project from zero, in about a day. Updated once
per stage (spec 14.1) -- this revision covers E1-E2.

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
(`POST /analyses`, multipart) → watch its position over
`GET /ws/analyses/{id}`. `api/openapi.yaml` has the exact request/response
shape for every endpoint.

## 3. Where things live

See the table in `README.md`. The one rule worth internalizing before
writing any Go: dependencies point inward --
`transport → service → repository → domain` -- and interfaces are declared
by the consumer (`service`), implemented by the infrastructure package
(`repository`, `security`, `mailer`, `oauth`). If you're writing an interface
next to its implementation instead of next to its caller, it's in the wrong
place.

## 4. Before your first PR

- `cd api && go test ./... && go test -tags=integration ./... && gofmt -l . && go vet ./... && golangci-lint run`
- Read `docs/REVIEW_CHECKLIST.md` -- that's what gets checked.
- Commit format is enforced by `.githooks/commit-msg`
  (`git config core.hooksPath .githooks` after cloning -- do this before your
  first commit). See `CLAUDE.md` for the exact format.
- One PR = one `FR-*` requirement, diff under ~400 lines.

## 5. What doesn't exist yet

`worker/` (Python ML pipeline, stage E3) and `web/` (React frontend, stage
E5) are empty. That means: analysis jobs queue and sit at `status=queued`
forever (nothing consumes the Redis Streams queue yet), `retry` isn't
implemented (nothing can produce a `failed` job to retry), and there is no
browser UI for recording (FR-20) -- only the API surface for it. Check
`tech.md` section 18 for what each stage adds.
