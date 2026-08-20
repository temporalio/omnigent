#!/usr/bin/env bash
# Shared settings for the dev scripts. Override any of these in your shell.
#
# Everything is deliberately off the default ports and out of the default
# database, so running these cannot disturb an Omnigent or Temporal you already
# use. Only logs land in the usual place (~/.omnigent/logs).

set -euo pipefail

DEV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$DEV_DIR/.." && pwd)"
REPO_DIR="$(cd "$PKG_DIR/.." && pwd)"

: "${TEMPORAL_PORT:=7255}"
: "${TEMPORAL_UI_PORT:=8255}"
: "${TEMPORAL_ADDRESS:=127.0.0.1:$TEMPORAL_PORT}"

: "${OMNIGENT_PORT:=8797}"
: "${OMNIGENT_SERVER_URL:=http://127.0.0.1:$OMNIGENT_PORT}"

# The sandbox the agent works in, and where its database and artifacts live.
: "${DEV_STATE_DIR:=/tmp/omnigent-temporal-dev}"
: "${OMNIGENT_WORKSPACE:=$DEV_STATE_DIR/workspace}"

# openai-agents is the OpenAI-backed harness, and it is not a native one, so the
# runner-recovery path this executor adds is actually reachable with it.
: "${OMNIGENT_AGENT:=openai-echo}"
: "${AGENT_FILE:=$DEV_DIR/agents/openai-echo.yaml}"

VENV="$PKG_DIR/.venv"
PY="$VENV/bin/python"
OMNIGENT="$VENV/bin/omnigent"

export TEMPORAL_ADDRESS OMNIGENT_SERVER_URL OMNIGENT_AGENT OMNIGENT_WORKSPACE

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

need_venv() {
  [ -x "$PY" ] || die "no venv yet. Run: $DEV_DIR/setup.sh"
}

need_key() {
  [ -n "${OPENAI_API_KEY:-}" ] || die "OPENAI_API_KEY is not set. The agent needs it to answer."
}

port_free() {
  ! nc -z 127.0.0.1 "$1" >/dev/null 2>&1
}

need_free_port() {
  local port="$1" what="$2"
  port_free "$port" && return 0
  die "port $port is already in use, so $what cannot start.
  Something else is on it (another Omnigent, or another copy of these scripts).
  Either stop it, or point these scripts elsewhere:
      OMNIGENT_PORT=8799 TEMPORAL_PORT=7259 $0"
}

wait_for_http() {
  local url="$1" tries="${2:-60}"
  for _ in $(seq 1 "$tries"); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then return 0; fi
    sleep 1
  done
  return 1
}
