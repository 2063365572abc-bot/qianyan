#!/usr/bin/env sh
set -eu
root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_file=${1:?Usage: restore-db.sh BACKUP.dump CONFIRM_REPLACE_DATABASE}
confirmation=${2:-}
if [ "$confirmation" != "CONFIRM_REPLACE_DATABASE" ]; then
  echo "Restore replaces current database contents. Back up first, then supply CONFIRM_REPLACE_DATABASE." >&2
  exit 1
fi
if [ ! -f "$backup_file" ] || [ ! -s "$backup_file" ]; then
  echo "Backup file is missing or empty." >&2
  exit 1
fi
docker compose --env-file "$root_dir/.env" -f "$root_dir/infra/compose.yaml" stop api worker
docker compose --env-file "$root_dir/.env" -f "$root_dir/infra/compose.yaml" exec -T postgres pg_restore -U qianyan -d qianyan --clean --if-exists --no-owner --no-acl --exit-on-error < "$backup_file"
docker compose --env-file "$root_dir/.env" -f "$root_dir/infra/compose.yaml" run --rm migrate
docker compose --env-file "$root_dir/.env" -f "$root_dir/infra/compose.yaml" up -d api worker
echo "Restore completed. Verify goals, evidence and worker readiness before resuming use."
