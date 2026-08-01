# Runbook

Deploy, rollback, backup restore, and an incident log (symptom → cause →
action → prevention), updated after every incident (spec 14.1, 17.1).

## Server setup (one-time, before the first deploy)

Spec 11.1's perimeter controls outside `docker compose` itself -- there is
no code that can enforce these, so they are a manual checklist for whoever
provisions the VPS the first time:

1. **Cloudflare** in front of the VPS: DNS-only record switched to
   "proxied" for the app's domain. Gives DNS proxying, basic DDoS
   absorption, and hides the origin IP -- it sits in front of the compose
   stack, not inside it, so it does not conflict with "everything on one
   VPS".
2. **UFW, deny by default:**
   ```bash
   sudo ufw default deny incoming
   sudo ufw default allow outgoing
   sudo ufw allow OpenSSH
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable
   ```
   Only 80/443 (Caddy) and SSH are ever exposed -- Postgres and Redis are
   never published to the host in the first place (`deploy/docker-compose.yml`
   has no `ports:` for either), so there is nothing to block for them
   specifically.
3. **SSH hardening** (`/etc/ssh/sshd_config`, then `systemctl restart sshd`):
   ```
   PasswordAuthentication no
   PermitRootLogin no
   ```
   Key-based auth only, deploy user is not `root`.
4. **fail2ban** against SSH brute force:
   ```bash
   sudo apt-get install -y fail2ban
   sudo systemctl enable --now fail2ban
   ```
   The stock `sshd` jail that ships with fail2ban is enough; nothing else
   in this stack listens on a host-exposed port for fail2ban to watch.

Redo this checklist for every new VPS; nothing here is part of
`docker compose up` because none of it is portable across hosts.

## Deploy (with automatic rollback)

Tag the release on `main` first (spec 13.5: SemVer, e.g. `v0.6.0`,
`CHANGELOG.md` updated in the same PR), then, on the VPS as the deploy user:

```bash
git fetch --tags
deploy/deploy.sh v0.6.0
```

`deploy/deploy.sh` implements spec 16.2's sequence end to end:

1. Checks out the given ref (refuses if the working tree isn't clean).
2. `docker compose -f deploy/docker-compose.yml up -d --build`. `go-api`
   applies any pending goose migrations itself on boot, before it starts
   accepting requests -- there is no separate migrate step.
3. Polls `go-api`'s own container `HEALTHCHECK` (gated on `/readyz`, which
   is gated on migrations having applied) for up to 60s.
4. If it never reports healthy, checks out whatever ref was running before
   this deploy, rebuilds, and polls again. Exit code distinguishes a clean
   rollback (`1`) from a rollback that *also* failed to come up healthy
   (`2`, needs a human -- see "Incidents" below).

Watch it live in another terminal:
`docker compose -f deploy/docker-compose.yml logs -f go-api`.

Migrations are written expand/contract (backward compatible for one release,
spec 7/16.2), so rolling the image back one release never leaves the schema
in a state the older code can't read -- this is what makes step 4's
automatic rollback safe to do without a human checking the schema first.

**Verified:** rehearsed locally (a fresh clone stood in for the VPS, since
this project has no provisioned production server or staging environment
yet, spec 16.3) -- both the success path and a forced failure (an
intentionally broken ref) were exercised; the failure case correctly rolled
back and exited `1`, and `go-api` came back up serving the previous ref.
No CD automation triggers this script; it is run by hand, matching this
stage's CI-only scope (spec 16.1 already covers automated lint/test/
security/build on every PR -- deploy stays a deliberate, manual action).

## Rollback (manual)

If you need to roll back without re-running the full script (e.g. days
later, not right after a failed deploy):

```bash
git checkout <previous-tag>
docker compose -f deploy/docker-compose.yml up -d --build
```

Same expand/contract guarantee as above applies.

## Restore from backup

The `backup` service writes a nightly `pg_dump | gzip` to `./backups`
(14-day local retention). To restore:

```bash
docker compose -f deploy/docker-compose.yml stop go-api
gunzip -c backups/<file>.sql.gz | docker compose -f deploy/docker-compose.yml exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker compose -f deploy/docker-compose.yml start go-api
```

Per spec 17.1, do a full practice restore once a month on a local machine
(not the production DB) and record the result below -- a backup that has
never been restored is not a backup.

| Date | Result | Notes |
|---|---|---|
| -- | -- | first restore drill not yet performed (stage E1, no production data yet) |

## Rotating a leaked secret

1. Generate a new value, update `.env` on the server (permissions `600`).
2. `docker compose -f deploy/docker-compose.yml up -d` (recreates the
   affected containers with the new value).
3. If `JWT_SECRET` leaked: every previously issued access token is now
   invalid the moment the new secret is live (by design -- old tokens fail
   signature verification). Also revoke all refresh-token families, since a
   leaked JWT secret is often a sign the whole host was compromised:
   `docker compose -f deploy/docker-compose.yml exec redis redis-cli -a "$REDIS_PASSWORD" --scan --pattern 'auth:refresh*' | xargs -r docker compose -f deploy/docker-compose.yml exec -T redis redis-cli -a "$REDIS_PASSWORD" DEL`
4. If `POSTGRES_PASSWORD`/`REDIS_PASSWORD` leaked: rotate in `.env`, then
   `ALTER USER ... PASSWORD` / `CONFIG SET requirepass` before recreating
   containers, so there's no window where the old and new credentials both
   need to work.

## Incidents

None yet -- stage E1 has not been operated in production.

### 2026-08-01 -- song stuck at "waiting for song to be ready" (dev environment)

**Symptom:** a song's cold-path prep and every analysis waiting on it sat on
"waiting for song to be ready" for several minutes, then flipped to
`failed` / `TIMEOUT`.

**Cause:** two independent issues in the dev compose stack:

1. `prep_reference_pitch` (CREPE, CPU) kept exceeding its 120s timeout
   (`PREP_REFERENCE_PITCH_TIMEOUT_SECONDS`) on CPU-only dev hardware.
   `PipelineRunner` retried it 3 times before failing the job -- that retry
   sequence is what produced the multi-minute wait before the terminal
   `failed` state.
2. `transcribe` was separately crashing with `ModuleNotFoundError: No
   module named 'faster_whisper'` (optional stage, skipped, not the
   blocker): the `python-worker` image had been built before the commit
   that swapped `openai-whisper` for `faster-whisper`, and
   `deploy/docker-compose.dev.yml`'s anonymous `/src/.venv` volume carries
   the old venv forward across a plain `up --build` unless anonymous
   volumes are explicitly renewed.

**Action:** rebuilt the worker image with
`docker compose -f deploy/docker-compose.dev.yml up -d --build --renew-anon-volumes python-worker`;
set `PITCH_ENGINE=pyin` in the local (gitignored) `.env` for this CPU-only
machine. Verified against the stem that had timed out: pyin processed a
330s reference track in 76s, crepe never finished inside 120s.

**Prevention:** after any change to `worker/pyproject.toml` /
`worker/uv.lock`, rebuild with `--renew-anon-volumes` -- a plain `--build`
alone reuses the old anonymous `.venv` volume and silently keeps stale
dependencies. Keep `PITCH_ENGINE=crepe` only on dev hardware that can
actually clear a multi-minute song inside
`PREP_REFERENCE_PITCH_TIMEOUT_SECONDS`; default to `pyin` otherwise.
