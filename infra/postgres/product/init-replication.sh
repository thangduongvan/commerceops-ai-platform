#!/bin/bash
# V6 (Database HA): configure the primary for streaming replication so the
# db-replica Compose service can take a base backup and stay in recovery.
# Mounted into /docker-entrypoint-initdb.d — runs once, on first init of an
# empty data directory. See docs/adr/ADR-007-database-ha.md.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Dedicated replication role. REPLICATION lets it use the streaming
    -- protocol; LOGIN is required; it does not need (and should not have)
    -- SUPERUSER. Password matches the primary's app password for local
    -- simplicity — never do this outside a learning project.
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replicator') THEN
            CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'commerceops';
        END IF;
    END
    \$\$;

    -- Physical replication slot so the primary retains WAL the standby has
    -- not yet consumed. Without a slot, a restarted or lagging standby can
    -- fall behind past the primary's WAL retention and become unrecoverable
    -- without a fresh base backup.
    SELECT pg_create_physical_replication_slot('commerceops_standby')
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_replication_slots WHERE slot_name = 'commerceops_standby'
    );
EOSQL

# Remote replication connections match *only* `host replication ...` lines
# in pg_hba.conf — the image's default `host all all all scram-sha-256`
# does NOT cover the replication protocol. Append (don't replace) so the
# existing app-user rules stay intact.
# PGDATA is set by the official postgres image.
echo "host replication replicator all scram-sha-256" >> "${PGDATA}/pg_hba.conf"
