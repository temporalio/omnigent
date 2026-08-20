#!/usr/bin/env bash
set -euo pipefail

# A local Temporal server for the executor to talk to.
#
# The database file is kept on purpose: workflow history has to survive a server restart, or you
# cannot tell a durability bug from a fresh start.

PORT="${TEMPORAL_PORT:-7237}"
UI_PORT="${TEMPORAL_UI_PORT:-8237}"
DB="${TEMPORAL_DB:-/tmp/omnigent-temporal/temporal.db}"

mkdir -p "$(dirname "$DB")"
echo "temporal on 127.0.0.1:${PORT}   ui http://127.0.0.1:${UI_PORT}   db ${DB}"
exec temporal server start-dev --port "$PORT" --ui-port "$UI_PORT" --db-filename "$DB"
