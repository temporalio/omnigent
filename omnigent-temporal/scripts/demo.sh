#!/usr/bin/env bash
set -euo pipefail

# One turn, end to end: submit a prompt to a session key and wait for the answer.
# Usage: scripts/demo.sh [session-key] [prompt]

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
VENV="${OMNIGENT_VENV:-$ROOT/.venv}"
KEY="${1:-demo-$(date +%s)}"
PROMPT="${2:-Reply with the single word FOXTROT.}"

export TEMPORAL_ADDRESS="${TEMPORAL_ADDRESS:-127.0.0.1:7237}"
export OMNIGENT_SERVER_URL="${OMNIGENT_SERVER_URL:-http://127.0.0.1:${OMNIGENT_PORT:-8791}}"

# Outlast the worker's sub-agent gate, which can hold an answered turn open until its timeout.
WAIT_SECONDS="${WAIT_SECONDS:-$(( ${OMNIGENT_SUBTREE_TIMEOUT_SECONDS:-120} + 180 ))}"

cd "$ROOT"
"$VENV/bin/python" -m omnigent_temporal submit "$KEY" "$PROMPT"
echo "waiting for the turn to finish (Ctrl+C is safe, the turn keeps going)"
for _ in $(seq 1 $(( WAIT_SECONDS / 5 ))); do
  OUT="$("$VENV/bin/python" -m omnigent_temporal state "$KEY" 2>/dev/null || true)"
  if printf '%s' "$OUT" | grep -q '"finished": {'; then
    printf '%s\n' "$OUT"
    exit 0
  fi
  sleep 5
done
echo "no answer within the wait window; check the worker output" >&2
"$VENV/bin/python" -m omnigent_temporal state "$KEY" || true
exit 1
