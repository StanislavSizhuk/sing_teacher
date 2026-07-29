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
COPY --from=builder /src/dist /srv/www
