#!/bin/sh
# Nightly pg_dump to ./backups, gzip-compressed, 14-day local retention
# (spec 17.1). A plain sleep loop instead of real cron: postgres:16-alpine has
# no cron package, and one job a day needs nothing fancier.
set -eu

export PGPASSWORD="$POSTGRES_PASSWORD"

while true; do
  timestamp=$(date +%Y%m%d_%H%M%S)
  dest="/backups/${POSTGRES_DB}_${timestamp}.sql.gz"

  if pg_dump -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" | gzip > "$dest"; then
    echo "backup: wrote $dest"
  else
    echo "backup: pg_dump failed" >&2
    rm -f "$dest"
  fi

  find /backups -name '*.sql.gz' -mtime +14 -delete
  sleep 86400
done
