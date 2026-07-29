# syntax=docker/dockerfile:1

# node:22-alpine, not node:22 (Debian) -- the final stage is Caddy's own
# Alpine base, and there is no native-addon dependency here forcing glibc
# the way worker/Dockerfile's torch/demucs wheels do.
ARG NODE_IMAGE=node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2

FROM ${NODE_IMAGE} AS dev
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
EXPOSE 5173
# --host so the dev server binds 0.0.0.0: without it Vite only listens on
# the container's loopback, unreachable from the host (deploy/docker-compose.dev.yml).
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM ${NODE_IMAGE} AS builder
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Production has no separate web container (spec 5.1: Caddy "одразу віддає
# статику React -- на один контейнер менше"). This stage's image IS what
# deploy/docker-compose.yml's `caddy` service builds and runs; ADR-0013.
FROM caddy:2-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648
# curl (and its libcurl/c-ares dependencies) ship in the upstream image but
# nothing here calls them: the compose healthcheck uses busybox's wget, and
# the static Caddy binary doesn't link against them. Removing the package
# outright -- rather than apk-upgrading it -- drops CVE-2026-5773/6276
# (curl/libcurl) and CVE-2026-33630 (c-ares) instead of just deferring them.
RUN apk del --no-cache curl libcurl c-ares
COPY --from=builder /src/dist /srv/www
