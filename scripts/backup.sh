#!/usr/bin/env bash
# Backchannel — backup script.
#
# Uses SQLite's `.backup` (online, lock-free for readers) to take a
# consistent snapshot of the running database into a gzipped file
# named with the UTC timestamp.
#
# Usage:
#   ./backup.sh [--db /data/backchannel.db] [--out /backups]
#
# Cron example (daily at 02:00):
#   0 2 * * * /opt/backchannel/scripts/backup.sh \
#               --db /data/backchannel.db --out /backups

set -euo pipefail

DB="${BACKCHANNEL_DB:-/data/backchannel.db}"
OUT_DIR="${BACKCHANNEL_BACKUP_DIR:-./backups}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db) DB="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -f "$DB" ]]; then
  echo "database not found: $DB" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
TS=$(date -u +%Y%m%dT%H%M%SZ)
SNAPSHOT="$OUT_DIR/.backchannel-$TS.sqlite"
TARGET="$OUT_DIR/backchannel-$TS.sqlite.gz"

# `.backup` is the online consistent-snapshot command. Even under writes,
# it captures a coherent state. Then gzip to keep transfer cheap.
sqlite3 "$DB" ".backup '$SNAPSHOT'"
gzip -9 "$SNAPSHOT"
mv "$SNAPSHOT.gz" "$TARGET"

# Retention: keep the latest 30 by mtime. Adjust BACKCHANNEL_BACKUP_KEEP.
KEEP="${BACKCHANNEL_BACKUP_KEEP:-30}"
ls -1t "$OUT_DIR"/backchannel-*.sqlite.gz 2>/dev/null \
  | tail -n +$((KEEP + 1)) \
  | xargs -r rm -f

echo "ok backup=$TARGET bytes=$(wc -c < "$TARGET")"
