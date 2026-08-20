#!/usr/bin/env bash
# 2 of 3: the Omnigent server plus a host. Leave this running.
#
# Both are needed. The server holds the sessions; the host is what launches a
# runner for each one. Without a host every turn fails runner_failed_to_start.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
need_venv
need_key

need_free_port "$OMNIGENT_PORT" "the Omnigent server"

# A host daemon outlives the shell that started it, so a previous run can still
# be attached to this URL. Clear ours before starting a new one.
"$OMNIGENT" host stop --server "$OMNIGENT_SERVER_URL" >/dev/null 2>&1 || true

mkdir -p "$OMNIGENT_WORKSPACE" "$DEV_STATE_DIR/artifacts"

cleanup() {
  log "stopping the host and server"
  "$OMNIGENT" host stop --server "$OMNIGENT_SERVER_URL" >/dev/null 2>&1 || true
  [ -n "${HOST_PID:-}" ] && kill "$HOST_PID" 2>/dev/null || true
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

log "Omnigent server on $OMNIGENT_SERVER_URL (db + artifacts under $DEV_STATE_DIR)"
"$OMNIGENT" server \
  --host 127.0.0.1 --port "$OMNIGENT_PORT" \
  --database-uri "sqlite:///$DEV_STATE_DIR/omnigent.db" \
  --artifact-location "$DEV_STATE_DIR/artifacts" \
  --agent "$AGENT_FILE" &
SERVER_PID=$!

wait_for_http "$OMNIGENT_SERVER_URL/health" 90 || die "the server never came up; see ~/.omnigent/logs/server/"
log "server is up"

log "registering this machine as a host (it launches the runners)"
"$OMNIGENT" host --server "$OMNIGENT_SERVER_URL" --non-interactive &
HOST_PID=$!

log "ready. Agent '$OMNIGENT_AGENT' in $OMNIGENT_WORKSPACE. Ctrl-C to stop both."
wait
