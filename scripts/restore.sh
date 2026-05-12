#!/usr/bin/env bash
# Backchannel — restore script.
#
# Restores a gzipped backup over the live database. STOPS the service
# first via docker-compose. Refuses to overwrite without --force.
#
# Usage:
#   ./restore.sh --from /backups/backchannel-20260512T020000Z.sqlite.gz \
#                --to   /data/backchannel.db \
#                [--compose-file docker-compose.self-host.yml] \
#                [--force]

set -euo pipefail

FROM=""
TO=""
COMPOSE="${BACKCHANNEL_COMPOSE_FILE:-docker-compose.self-host.yml}"
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --to) TO="$2"; shift 2 ;;
    --compose-file) COMPOSE="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$FROM" || -z "$TO" ]]; then
  echo "usage: $0 --from <backup.gz> --to <db.path> [--force]" >&2
  exit 1
fi
if [[ ! -f "$FROM" ]]; then
  echo "backup not found: $FROM" >&2
  exit 1
fi
if [[ -f "$TO" && $FORCE -eq 0 ]]; then
  echo "target exists: $TO — re-run with --force to overwrite" >&2
  exit 1
fi

echo "▸ stopping backchannel containers (compose file: $COMPOSE)"
docker compose -f "$COMPOSE" stop app worker || true

TMP=$(mktemp -t backchannel-restore.XXXXXX)
trap 'rm -f "$TMP"' EXIT

echo "▸ decompressing snapshot"
gunzip -c "$FROM" > "$TMP"

echo "▸ verifying SQLite integrity of snapshot"
INTEGRITY=$(sqlite3 "$TMP" "PRAGMA integrity_check;")
if [[ "$INTEGRITY" != "ok" ]]; then
  echo "snapshot is corrupt: $INTEGRITY" >&2
  exit 2
fi

if [[ -f "$TO" ]]; then
  echo "▸ saving current db as $TO.pre-restore"
  cp "$TO" "$TO.pre-restore"
fi

echo "▸ writing snapshot to $TO"
mv "$TMP" "$TO"
trap - EXIT

echo "▸ restarting backchannel containers"
docker compose -f "$COMPOSE" up -d

echo "ok restored from=$FROM to=$TO"
