#!/usr/bin/env bash
# Stop everything these scripts start. Safe to run twice.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

log "stopping the worker"
stop_recorded worker

if [ -x "$OMNIGENT" ]; then
  log "stopping the host daemon for $OMNIGENT_SERVER_URL"
  "$OMNIGENT" host stop --server "$OMNIGENT_SERVER_URL" >/dev/null 2>&1 || true
  rm -f "$PIDS_DIR/host.pid"
fi

log "stopping the Omnigent server on port $OMNIGENT_PORT"
stop_recorded server

log "stopping the Temporal server on port $TEMPORAL_PORT"
stop_recorded temporal

log "only what these scripts started was touched; anything else on this machine is untouched"
log "done. State is still in $DEV_STATE_DIR; delete it for a clean slate."
