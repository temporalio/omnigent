#!/usr/bin/env bash
set -euo pipefail

# The Temporal worker: hosts the OmnigentSession workflow and the turn activities. Point it at the
# server and host that scripts/omnigent-up.sh started.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
VENV="${OMNIGENT_VENV:-$ROOT/.venv}"
OMNI_DATA="${OMNI_DATA:-/tmp/omnigent-temporal}"

export TEMPORAL_ADDRESS="${TEMPORAL_ADDRESS:-127.0.0.1:7237}"
export OMNIGENT_SERVER_URL="${OMNIGENT_SERVER_URL:-http://127.0.0.1:${OMNIGENT_PORT:-8791}}"
export OMNIGENT_AGENT="${OMNIGENT_AGENT:-codex-native-ui}"
export OMNIGENT_WORKSPACE="${OMNIGENT_WORKSPACE:-$OMNI_DATA/workspace}"
# Shorter than the 600s default: for a hand test, a wedged sub-agent should give up sooner.
export OMNIGENT_SUBTREE_TIMEOUT_SECONDS="${OMNIGENT_SUBTREE_TIMEOUT_SECONDS:-120}"
mkdir -p "$OMNIGENT_WORKSPACE"

echo "runs in the foreground; give it its own terminal"
echo "temporal  $TEMPORAL_ADDRESS"
echo "omnigent  $OMNIGENT_SERVER_URL   agent $OMNIGENT_AGENT   workspace $OMNIGENT_WORKSPACE"
cd "$ROOT"
exec "$VENV/bin/python" -m omnigent_temporal.worker
