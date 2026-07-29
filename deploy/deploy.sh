#!/usr/bin/env bash
# Deploys a git ref (tag vX.Y.Z, per spec 13.5/16.2) to this host and
# automatically rolls back to whatever ref was running before if go-api
# doesn't report healthy within readyz_timeout_seconds.
#
# Sequence (spec 16.2): checkout -> build+up -d -> poll go-api's own
# HEALTHCHECK (which is gated on /readyz, which is gated on migrations
# having applied -- go-api runs goose itself on boot, so there is no
# separate migrate step) -> roll back on timeout.
#
# Usage: deploy/deploy.sh <git-ref>
# Run from the repo root, on the VPS, as the deploy user. Requires a clean
# working tree (nothing here is meant to run against uncommitted changes).
set -euo pipefail

readyz_timeout_seconds=60
poll_interval_seconds=2
compose_file=deploy/docker-compose.yml
service=go-api

log() {
  printf '%s deploy: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

usage() {
  echo "usage: $0 <git-ref>" >&2
  exit 1
}

[ "$#" -eq 1 ] || usage
new_ref=$1

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

command -v docker >/dev/null || die "docker is not on PATH"
command -v git >/dev/null || die "git is not on PATH"
[ -f "$compose_file" ] || die "$compose_file not found -- run this from the repo root"
[ -f .env ] || die ".env not found -- copy .env.example and fill it in first"

if [ -n "$(git status --porcelain)" ]; then
  die "working tree is not clean -- commit or stash before deploying"
fi

git rev-parse --verify --quiet "${new_ref}^{commit}" >/dev/null \
  || die "ref '$new_ref' does not exist locally -- git fetch --tags first"

# What's running right now, so a failed deploy has something concrete to
# fall back to. Falls back to the current commit if HEAD isn't exactly a
# tag (e.g. the very first deploy on a fresh checkout).
previous_ref=$(git describe --tags --exact-match 2>/dev/null || git rev-parse HEAD)

wait_for_healthy() {
  local deadline=$((SECONDS + readyz_timeout_seconds))
  while [ "$SECONDS" -lt "$deadline" ]; do
    status=$(docker compose -f "$compose_file" ps --format '{{.Health}}' "$service" 2>/dev/null || true)
    if [ "$status" = "healthy" ]; then
      return 0
    fi
    if [ "$status" = "unhealthy" ]; then
      return 1
    fi
    sleep "$poll_interval_seconds"
  done
  return 1
}

deploy_ref() {
  local ref=$1
  log "checking out $ref"
  git checkout --quiet "$ref"
  log "building and starting the stack at $ref"
  docker compose -f "$compose_file" up -d --build
}

log "currently deployed ref: $previous_ref"
deploy_ref "$new_ref"

log "waiting up to ${readyz_timeout_seconds}s for $service to report healthy"
if wait_for_healthy; then
  log "deploy of $new_ref succeeded"
  exit 0
fi

log "$service did not become healthy within ${readyz_timeout_seconds}s -- rolling back to $previous_ref"
log "recent $service logs:"
docker compose -f "$compose_file" logs --tail 50 "$service" || true

deploy_ref "$previous_ref"

if wait_for_healthy; then
  log "rollback to $previous_ref succeeded -- $new_ref was NOT left running"
  exit 1
fi

log "ROLLBACK ALSO FAILED -- manual intervention required (docs/RUNBOOK.md)"
docker compose -f "$compose_file" logs --tail 50 "$service" || true
exit 2
