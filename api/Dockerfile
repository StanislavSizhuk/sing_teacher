# syntax=docker/dockerfile:1

FROM golang:1.26-alpine@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS dev
WORKDIR /src
# Unpinned here (unlike the runtime stage's exact NVRAs): this image tracks
# whatever Alpine release golang:1.26-alpine itself is built on, which the
# runtime stage's alpine:3.21 pin does not match. Acceptable for dev -- this
# stage never faces the internet (deploy/docker-compose.dev.yml).
RUN apk add --no-cache ffmpeg yt-dlp
RUN go install github.com/air-verse/air@v1.61.7
COPY go.mod go.sum ./
RUN go mod download
COPY . .
EXPOSE 8080
CMD ["air", "-c", ".air.toml"]

FROM golang:1.26-alpine@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS builder
WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .
# CGO disabled: every dependency (pgx, go-redis, argon2, goose w/ modernc.org
# sqlite) is pure Go, so a fully static binary is possible.
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/api ./cmd/api

# Runtime is Alpine, not distroless (see ADR-0007): stage E2 needs ffmpeg and
# yt-dlp on PATH to shell out to as argument lists (spec 11.3), and both are
# real binaries with their own runtime deps (yt-dlp needs a Python
# interpreter) that a shell-less, package-manager-less distroless image
# cannot host. Versions are pinned for the same reason images are pinned by
# digest below: reproducible, auditable builds.
FROM alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d
# yt-dlp comes from pip, not apk (see ADR-0035): it tracks YouTube's
# frequently-changing extraction internals, and apk's community repo lags
# upstream releases by months -- long enough that YouTube import breaks
# outright. pip installs a pure-Python wheel (no compiler needed) pinned to
# an exact upstream version; py3-pip itself is removed again afterward so it
# never ships in the final image, only the python3 interpreter yt-dlp needs
# to run and the yt-dlp package pip placed in its site-packages.
RUN apk add --no-cache ffmpeg=6.1.2-r1 python3=3.12.13-r0 py3-pip=24.3.1-r0 \
    && pip install --break-system-packages --no-cache-dir yt-dlp==2026.07.04 \
    && apk del py3-pip \
    && addgroup -S vocalcoach && adduser -S -G vocalcoach vocalcoach \
    && mkdir -p /data/audio-tmp && chown vocalcoach:vocalcoach /data/audio-tmp
COPY --from=builder /out/api /api
USER vocalcoach:vocalcoach
ENTRYPOINT ["/api"]
