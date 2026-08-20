#!/usr/bin/env bash
# Stop everything these scripts start. Safe to run twice.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

log "stopping the worker"
pkill -f "omnigent_temporal.worker" 2>/dev/null || true

if [ -x "$OMNIGENT" ]; then
  log "stopping the host daemon for $OMNIGENT_SERVER_URL"
  "$OMNIGENT" host stop --server "$OMNIGENT_SERVER_URL" >/dev/null 2>&1 || true
fi

log "stopping the Omnigent server on port $OMNIGENT_PORT"
pkill -f "omnigent server --host 127.0.0.1 --port $OMNIGENT_PORT" 2>/dev/null || true

log "stopping the Temporal server on port $TEMPORAL_PORT"
pkill -f "temporal server start-dev --port $TEMPORAL_PORT" 2>/dev/null || true

log "done. State is still in $DEV_STATE_DIR; delete it for a clean slate."
