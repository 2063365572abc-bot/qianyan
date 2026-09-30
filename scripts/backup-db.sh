#!/usr/bin/env sh
set -eu
umask 077
root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${1:-"$root_dir/.local/backups"}
mkdir -p "$backup_dir"
backup_file="$backup_dir/qianyan-$(date -u +%Y%m%dT%H%M%SZ).dump"
# Redirection runs on the host, not in Docker. Never print .env/config expansion.
docker compose --env-file "$root_dir/.env" -f "$root_dir/infra/compose.yaml" exec -T postgres pg_dump -U qianyan -d qianyan -Fc > "$backup_file"
if [ ! -s "$backup_file" ]; then
  echo "Backup is empty; do not use it." >&2
  exit 1
fi
echo "Backup created: $backup_file"
echo "Copy it to separate encrypted storage; a local volume is not disaster recovery."
