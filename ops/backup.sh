#!/usr/bin/env sh
set -eu
umask 077

if [ "$#" -ne 2 ]; then
  echo "usage: backup.sh DATABASE_URL OUTPUT.dump" >&2
  exit 2
fi

database_url="$1"
output="$2"
pg_dump --format=custom --no-owner --no-acl "$database_url" --file "$output"
sha256sum "$output" > "$output.sha256"
echo "backup written with checksum: $output"
