#!/usr/bin/env bash
set -euo pipefail
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$OUT_DIR"
FILE="$OUT_DIR/phoe_lone_$STAMP.sql.gz"
docker compose exec -T postgres pg_dump -U phoe phoe_lone | gzip > "$FILE"
find "$OUT_DIR" -name 'phoe_lone_*.sql.gz' -mtime +14 -delete
echo "wrote $FILE"
