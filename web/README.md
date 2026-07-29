# web

React + TypeScript + Tailwind SPA: auth, song upload/YouTube import, browser
recording (MediaRecorder), and the analysis queue screen. See the repo root
[README](../README.md) and [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)
for how this fits into the rest of the stack.

`docker compose -f ../deploy/docker-compose.dev.yml up` already serves this
directory at `http://localhost:5173` with hot reload (`web`'s `Dockerfile`,
`dev` target); in production `deploy/Caddyfile` serves the build straight
out of Caddy's own image (ADR-0013). `npm run dev` below is for running
against `go-api` without Docker at all, e.g. while iterating on `web/` alone.

## Development

```bash
npm install
npm run dev          # http://localhost:5173, expects go-api on :8080 (see .env.example)
```

```bash
npm run typecheck    # tsc -b --noEmit
npm run lint         # eslint .
npm run format       # prettier --check .
npm test             # vitest run
npm run build        # tsc -b && vite build
npm run generate:api # regenerate src/api/schema.gen.ts from ../api/openapi.yaml
```

`src/api/schema.gen.ts` is generated, not hand-edited -- rerun
`generate:api` after any `api/openapi.yaml` change and commit the diff.

## Layout

| Path                     | Responsibility                                                     |
| ------------------------ | ------------------------------------------------------------------ |
| `src/api/`               | Generated OpenAPI types, the one typed fetch client, session store |
| `src/features/auth/`     | Register, verify, login                                            |
| `src/features/songs/`    | Add a song by file upload or YouTube link                          |
| `src/features/analysis/` | Browser recording (MediaRecorder), analysis queue status           |
| `src/components/`        | Shared presentational primitives                                   |
| `src/hooks/`             | Shared hooks not tied to one feature                               |
