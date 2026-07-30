#!/usr/bin/env bash
# Backup Postgres volume to a timestamped SQL dump on the host.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
OUT="backups/supply-$(date +%Y%m%d-%H%M%S).sql"
docker compose exec -T db pg_dump -U supply supply > "$OUT"
echo "Wrote $OUT"
