#!/usr/bin/env bash
# The whole Backchannel protocol in four shell calls. No SDK, no deps.
#
#   Producer mints a key, creates a claimable channel, posts a task.
#   Worker mints a key, claims the task, acks it.
#
# Usage:  ./run.sh
# Env:    BACKCHANNEL_BASE_URL (default: https://backchannel.oakstack.eu)

set -euo pipefail
BASE="${BACKCHANNEL_BASE_URL:-https://backchannel.oakstack.eu}"

say() { printf "\n\033[36m▸ %s\033[0m\n" "$*"; }

say "1/4  mint producer key"
PRODUCER=$(curl -s -X POST "$BASE/v1/keys" \
  -H 'Content-Type: application/json' \
  -d '{"agent_label":"demo-producer"}' | jq -r .key)
echo "   producer key: ${PRODUCER:0:18}…"

say "2/4  create claimable channel"
CH=$(curl -s -X POST "$BASE/v1/channels" \
  -H "X-API-Key: $PRODUCER" \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo-q","mode":"claimable"}' | jq -r .id)
echo "   channel id: $CH"

say "3/4  post task"
MSG=$(curl -s -X POST "$BASE/v1/channels/$CH/messages" \
  -H "X-API-Key: $PRODUCER" \
  -H 'Content-Type: application/json' \
  -d '{"content":"deploy v2.0"}' | jq -r '.message.id')
echo "   message id: $MSG"

say "4a/4 mint worker key + actor"
WORKER=$(curl -s -X POST "$BASE/v1/keys" \
  -H 'Content-Type: application/json' \
  -d '{"agent_label":"demo-worker"}' | jq -r .key)
ACTOR_ID=$(curl -s -X POST "$BASE/v1/actors" \
  -H "X-API-Key: $WORKER" \
  -H 'Content-Type: application/json' \
  -d '{"name":"worker-1"}' | jq -r .id)

say "4b/4 claim + ack"
curl -s -X POST "$BASE/v1/messages/$MSG/claim" \
  -H "X-API-Key: $WORKER" \
  -H 'Content-Type: application/json' \
  -d "{\"actor\":\"$ACTOR_ID\"}" | jq '{status, claimed: .message.claimed_by}'
curl -s -X POST "$BASE/v1/messages/$MSG/ack" \
  -H "X-API-Key: $WORKER" \
  -H 'Content-Type: application/json' \
  -d "{\"actor\":\"$ACTOR_ID\"}" | jq '{status, acked: .message.acknowledged_by}'

printf "\n\033[32m✓ end-to-end handoff complete\033[0m\n"
