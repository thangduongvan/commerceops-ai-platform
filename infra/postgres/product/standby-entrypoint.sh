#!/bin/bash
# V6 (Database HA): bootstrap (or resume) a hot standby from the Compose
# primary via pg_basebackup, then exec postgres in recovery. Same image as
# the primary; only this entrypoint differs. See docs/adr/ADR-007-database-ha.md.
set -euo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
PRIMARY_HOST="${PRIMARY_HOST:-product-db}"
REPLICATOR_USER="${REPLICATOR_USER:-replicator}"
REPLICATOR_PASSWORD="${REPLICATOR_PASSWORD:-commerceops}"
REPLICATION_SLOT="${REPLICATION_SLOT:-commerceops_standby}"

export PGPASSWORD="$REPLICATOR_PASSWORD"

# Official postgres image puts data under $PGDATA; an empty dir means we still
# need a base backup. A non-empty dir (after a previous successful bootstrap)
# already has standby.signal + postgresql.auto.conf from -R, so we just start.
if [ ! -f "${PGDATA}/PG_VERSION" ]; then
  echo "standby-entrypoint: waiting for primary at ${PRIMARY_HOST}:5432 ..."
  until pg_isready -h "$PRIMARY_HOST" -p 5432 -U "$REPLICATOR_USER" >/dev/null 2>&1; do
    sleep 1
  done

  # Extra settle time: pg_isready can succeed before the init script that
  # creates the replicator role / replication slot has finished.
  sleep 2

  echo "standby-entrypoint: taking base backup into ${PGDATA} ..."
  rm -rf "${PGDATA:?}/"*
  pg_basebackup \
    -h "$PRIMARY_HOST" \
    -p 5432 \
    -U "$REPLICATOR_USER" \
    -D "$PGDATA" \
    -Fp \
    -Xs \
    -R \
    -S "$REPLICATION_SLOT" \
    -w

  # -R wrote standby.signal and primary_conninfo into postgresql.auto.conf.
  # hot_standby must be on so the standby accepts read-only queries (the
  # whole point of the local replica for the app's read/write split).
  echo "hot_standby = on" >> "${PGDATA}/postgresql.auto.conf"
  echo "standby-entrypoint: base backup complete; starting in recovery"
fi

# Postgres refuses to start if PGDATA is group/world-accessible. Docker
# named volumes on some hosts (notably Docker Desktop on Windows) create
# the mount point as 0755; tighten it before exec.
chmod 0700 "$PGDATA" 2>/dev/null || true

exec postgres
