# Runbook

Deploy, rollback, backup restore, and an incident log (symptom → cause →
action → prevention), updated after every incident (spec 14.1, 17.1).

## Deploy (current: manual; CD arrives in E6)

```bash
git pull
docker compose -f deploy/docker-compose.yml up -d --build
```

`go-api` applies any pending goose migrations itself on boot, before it
starts accepting requests -- there is no separate migrate step. Watch
`docker compose -f deploy/docker-compose.yml logs -f go-api` for the
`migrations applied` line, then `listening`.

## Rollback

```bash
git checkout <previous-tag>
docker compose -f deploy/docker-compose.yml up -d --build
```

Migrations are written expand/contract (backward compatible for one
release, spec 7/16.2), so rolling the image back one release never leaves
the schema in a state the older code can't read.

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
