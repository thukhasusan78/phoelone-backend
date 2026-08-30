#!/usr/bin/env bash
set -euo pipefail
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT_DIR="${BACKUP_DIR:-/root/phoe_lone_server/backups}"
mkdir -p "$OUT_DIR"
FILE="$OUT_DIR/phoe_lone_$STAMP.sql.gz"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
set -a
source "$ROOT/.env"
set +a

if [[ "${DATABASE_URL:-}" == memory://* ]] || [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not PostgreSQL; refusing to dump." >&2
  exit 1
fi

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD missing in .env}"
pg_dump -h 127.0.0.1 -U phoe -d phoe_lone --no-owner --no-acl | gzip > "$FILE"
find "$OUT_DIR" -name 'phoe_lone_*.sql.gz' -mtime +14 -delete
echo "wrote $FILE"
