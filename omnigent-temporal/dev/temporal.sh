#!/usr/bin/env bash
# 1 of 3: a local Temporal server. Leave this running.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

command -v temporal >/dev/null || die "temporal CLI not found. brew install temporal"

need_free_port "$TEMPORAL_PORT" "the Temporal server"
mkdir -p "$DEV_STATE_DIR"

log "Temporal dev server on 127.0.0.1:$TEMPORAL_PORT (UI http://127.0.0.1:$TEMPORAL_UI_PORT)"
exec temporal server start-dev \
  --port "$TEMPORAL_PORT" \
  --ui-port "$TEMPORAL_UI_PORT" \
  --db-filename "$DEV_STATE_DIR/temporal.db"
