#!/bin/sh
set -eu

: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL is required}"

DATABASE_URL="$MIGRATION_DATABASE_URL" alembic upgrade head
psql "$MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 -f /app/ops/apply-runtime-grants.sql
