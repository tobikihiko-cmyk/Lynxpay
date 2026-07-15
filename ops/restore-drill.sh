#!/usr/bin/env sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: restore-drill.sh INPUT.dump TARGET_DATABASE_URL" >&2
  exit 2
fi

dump="$1"
target="$2"
case "$target" in
  *test*|*drill*) ;;
  *) echo "refusing restore: target URL must contain test or drill" >&2; exit 2 ;;
esac

sha256sum -c "$dump.sha256"
pg_restore --exit-on-error --clean --if-exists --no-owner --no-acl --dbname "$target" "$dump"
DATABASE_URL="$target" alembic upgrade head
echo "restore drill completed; run tenant/concurrency tests before destroying the target"
