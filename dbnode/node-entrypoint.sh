#!/bin/bash
# DB_ROLE=primary (standard): almindelig postgres-image.
# DB_ROLE=replica: første gang tages en basebackup af PRIMARY_HOST:PRIMARY_PORT (adgangskoden er primariens token),
#                  derefter starter postgres som hot standby, der streamer fra primary.
set -e
PGDATA="${PGDATA:-/var/lib/postgresql/data}"
if [ "$DB_ROLE" = "replica" ] && [ ! -s "$PGDATA/PG_VERSION" ]; then
  : "${PRIMARY_HOST:?PRIMARY_HOST mangler}"
  PRIMARY_PORT="${PRIMARY_PORT:-5432}"
  until pg_isready -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$POSTGRES_USER" >/dev/null 2>&1; do
    echo "Venter på primary $PRIMARY_HOST:$PRIMARY_PORT ..."; sleep 3
  done
  echo "Tager basebackup af $PRIMARY_HOST:$PRIMARY_PORT"
  mkdir -p "$PGDATA" && chown postgres:postgres "$PGDATA" && chmod 700 "$PGDATA"
  gosu postgres env PGPASSWORD="$POSTGRES_PASSWORD" \
    pg_basebackup -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$POSTGRES_USER" -D "$PGDATA" -X stream -R -P
fi
exec docker-entrypoint.sh "$@"
