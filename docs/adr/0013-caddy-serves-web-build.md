# ADR-0013: Caddy builds and serves web/'s static output directly

- Status: Accepted
- Date: 2026-07-29

## Context

Spec 5.1 picked Caddy over Nginx+Certbot partly because it can serve the
built React SPA itself -- "one less container." That was never actually
wired up: `deploy/docker-compose.yml`'s `caddy` service pulled the stock
`caddy:2-alpine` image and proxied every request to `go-api`, and `web/`
had no `Dockerfile` at all -- it only ran via a developer's own `npm run
dev`/`npm run build` outside `docker compose` entirely. `docs/ARCHITECTURE.md`
and `docs/SECURITY.md` both tracked this as a known, deliberately deferred
gap ("Not yet built" / E6's CSP review). Spec's own goal (5.2, NFR-10) is
that the whole stack comes up with one `docker compose up`; a stack that
silently excludes the frontend does not meet that bar.

## Decision

`web/Dockerfile` is multi-stage:

- `dev`: installs deps, runs the Vite dev server with `--host 0.0.0.0` for
  hot reload.
- `builder`: runs `npm run build` into `/src/dist`.
- final (default) stage: `FROM caddy:2-alpine@sha256:...`, with `/src/dist`
  copied in from `builder` to `/srv/www`. This image, not the plain
  `caddy:2-alpine` pull, is what `deploy/docker-compose.yml`'s `caddy`
  service builds and runs.

`deploy/Caddyfile` serves `/srv/www` with a `try_files` fallback to
`index.html`, and reverse-proxies `/api/*`, `/healthz`, `/readyz` to
`go-api`, all from one origin -- so `CORS_ALLOWED_ORIGIN` in production can
stay the same domain as `APP_BASE_URL`, no `unsafe-inline`/wildcard CSP
loosening needed, and browser cookies/fetches never cross an origin.

In development, a static build defeats the point (no hot reload), so
`deploy/docker-compose.dev.yml` instead gets its own `web` service built
from the same `Dockerfile`'s `dev` target, published on `:5173` like
`go-api` is on `:8080` -- the same shape the repo already uses for
`go-api`/`python-worker`'s dev-vs-prod split, just applied to `web/` for
the first time.

## Consequences

Gains: `docker compose -f deploy/docker-compose.yml up -d` and `docker
compose -f deploy/docker-compose.dev.yml up` each bring up the entire
stack, frontend included, per spec 5.2/NFR-10 -- no more manual `cd web &&
npm install && npm run dev` step undocumented outside compose. Caddy's CSP
now needs to be a real policy (`self`-scoped, with `blob:` for
`MediaRecorder` preview) instead of `default-src 'none'`, since it now
serves actual page content, not just proxies JSON. `deploy/deploy.sh`
needed no changes: it already runs `docker compose up -d --build`, which
rebuilds any service with a `build:` key, `caddy` included.

Loses: the `caddy` service's image is no longer a stock upstream pull;
every deploy rebuilds a small Node+Caddy image, adding the `web/` build to
the production build path. Rebuild cost is bounded (single-page Vite app),
already the same tradeoff `go-api`/`python-worker` accept.

## Alternatives considered

- A separate `web` container in production too (e.g. Nginx or a second
  Caddy instance serving static files, proxied to by the edge `caddy`) --
  rejected: reintroduces the extra container spec 5.1 explicitly avoided,
  for no benefit over building the SPA straight into the edge Caddy image.
- A shared named volume: a one-shot `web-builder` container writes
  `dist/` into a volume that `caddy` mounts read-only -- rejected: an extra
  compose service and a startup ordering dependency (`caddy` must not
  start serving before the one-shot build finishes) to save what a
  multi-stage `COPY --from=builder` already does for free at image-build
  time.
- Leaving `web/` as a developer-run process outside compose permanently --
  rejected: this is the exact gap being closed; it fails NFR-10 ("a new
  developer stands the project up locally within an hour, guided only by
  README.md") the moment the frontend isn't part of the one `docker
  compose up` command.
