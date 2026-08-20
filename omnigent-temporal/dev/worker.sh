#!/usr/bin/env bash
# 3 of 3: the durable executor. Leave this running.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
need_venv

wait_for_http "$OMNIGENT_SERVER_URL/health" 5 || die "Omnigent is not up. Run dev/omnigent.sh first."

log "worker against Temporal $TEMPORAL_ADDRESS and Omnigent $OMNIGENT_SERVER_URL"
# -u so the log is not buffered when this is piped or captured.
exec "$PY" -u -m omnigent_temporal.worker
