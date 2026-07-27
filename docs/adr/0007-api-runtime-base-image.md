# ADR-0007: go-api runtime base image moves from distroless to Alpine

- Status: Accepted
- Date: 2026-07-28

## Context

ADR-0006 chose `distroless/static-debian12` for the production `go-api`
image on the strength of every Go dependency being pure Go: no cgo, no
shared libraries, no shell, no package manager needed at runtime. Stage E2
(FR-10, FR-11) requires shelling out to `ffmpeg`, `ffprobe` and `yt-dlp` as
argument lists (spec 11.3) to validate, probe and canonicalize audio before
any of it is trusted. Distroless has no shell and no way to install those
binaries; the "one static binary" premise never accounted for external
tooling because E1 had none.

## Decision

`go-api`'s runtime stage is `alpine:3.21`, pinned by tag and digest, with
`ffmpeg` and `yt-dlp` installed via `apk` pinned to exact package versions
(`ffmpeg=6.1.2-r1`, `yt-dlp=2025.03.31-r0`). A non-root user is created
explicitly (Alpine has no built-in `nonroot` user the way distroless does).
The Go binary itself is still built with `CGO_ENABLED=0` in a separate
builder stage, so the binary itself remains fully static; only the runtime
image around it changes.

## Consequences

Gains: a real shell and package manager exist for `yt-dlp` (itself a Python
program) and `ffmpeg` to run at all. Loses: distroless's minimal-attack-surface
guarantee -- Alpine carries a shell, `apk`, and busybox utilities that
distroless deliberately excludes. This is mitigated the same way the rest of
the container hardening already works (spec 5.3): `cap_drop: [ALL]`,
`no-new-privileges`, `read_only: true` with an explicit writable volume only
for `/data/audio-tmp`, and a non-root user. `deploy.resources.limits.memory`
on the `go-api` service bounds the whole cgroup -- Go process plus any
spawned `ffmpeg`/`yt-dlp` child -- standing in for a per-process memory
ulimit, which `os/exec` cannot set on a child without a shell (spec 11.3).

ADR-0006's library choices (chi, pgx, go-redis, argon2id, golang-jwt,
oauth2/go-oidc) are unaffected and remain Accepted; only its "distroless is
possible" consequence is superseded by this ADR.

## Alternatives considered

- Keep distroless, run `python-worker` (E3) as the sole audio-processing
  surface -- rejected: FR-10/FR-11 (song upload and YouTube import) are
  `go-api` responsibilities in this stage's design (spec 5.4's `api/`
  layout), and deferring all audio validation to a worker that does not
  exist until E3 would leave two stages with no ingestion path at all.
- A separate sidecar container just for `ffmpeg`/`yt-dlp`, called over a
  local socket -- rejected: a second process/IPC boundary for two CLI tools
  is disproportionate complexity for a single-VPS, single-worker system
  (spec 5.1).
- `apk add` without exact version pins -- rejected for the production stage:
  inconsistent with "images pinned by tag and digest, `latest` forbidden"
  (spec 5.3) applied to package installs, not just base images. The `dev`
  stage (`golang:1.26-alpine`, a different Alpine release) intentionally
  stays unpinned there -- it never faces the internet (deploy/docker-compose.dev.yml).
