#!/usr/bin/env bash
set -euo pipefail

# Omnigent's own two processes: the server that owns the session store, and a host that launches
# the runner which actually executes a turn. Without the host, every turn fails with
# runner_failed_to_start, so both belong in one script.
#
# State is kept out of the way of a real Omnigent install: its own port, its own sqlite db, its
# own artifacts. Delete OMNI_DATA to start clean.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
VENV="${OMNIGENT_VENV:-$ROOT/.venv}"
PORT="${OMNIGENT_PORT:-8791}"
OMNI_DATA="${OMNI_DATA:-/tmp/omnigent-temporal}"
WORKSPACE="${OMNIGENT_WORKSPACE:-$OMNI_DATA/workspace}"

if [ ! -x "$VENV/bin/omnigent" ]; then
  echo "no omnigent in $VENV. Create it with:" >&2
  echo "  cd $ROOT && UV_NO_CONFIG=1 uv venv --python 3.12 && UV_NO_CONFIG=1 uv pip install temporalio -e ../sdks/python-client -e ." >&2
  exit 1
fi

# The key has to be in the HOST's environment, because the host is what spawns the runner that
# talks to the model. Exporting it in the shell you submit from does nothing.
if [ -z "${OPENAI_API_KEY:-}" ] && [ -r "$HOME/.config/ai363/llm.key" ]; then
  OPENAI_API_KEY="$(cat "$HOME/.config/ai363/llm.key")"
fi
if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "set OPENAI_API_KEY (or put a key in ~/.config/ai363/llm.key)" >&2
  exit 1
fi
export OPENAI_API_KEY
export CODEX_API_KEY="${CODEX_API_KEY:-$OPENAI_API_KEY}"

# The codex-native harness runs the codex CLI, and a host reports a harness as usable only when it
# can actually run it. Put a working codex first on PATH; its code-mode host has to sit next to it,
# which is why these are symlinked into one directory rather than added as two PATH entries.
CODEX_BIN="${CODEX_BIN:-}"
if [ -z "$CODEX_BIN" ]; then
  for candidate in "$HOME/.local/share/omnigent-test-bin/codex" "$(command -v codex || true)"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] && CODEX_BIN="$candidate" && break
  done
fi
if [ -x "$CODEX_BIN" ]; then
  SHIM="$OMNI_DATA/bin"
  mkdir -p "$SHIM"
  ln -sf "$CODEX_BIN" "$SHIM/codex"
  [ -x "${CODEX_BIN}-code-mode-host" ] && ln -sf "${CODEX_BIN}-code-mode-host" "$SHIM/codex-code-mode-host"
  export PATH="$SHIM:$PATH"
fi
echo "codex on PATH: $(command -v codex || echo none)"

mkdir -p "$OMNI_DATA/artifacts" "$WORKSPACE"
export UV_NO_CONFIG=1

cleanup() {
  [ -n "${HOST_PID:-}" ] && kill "$HOST_PID" 2>/dev/null || true
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$VENV/bin/omnigent" server --host 127.0.0.1 --port "$PORT" \
  --database-uri "sqlite:///$OMNI_DATA/chat.db" \
  --artifact-location "$OMNI_DATA/artifacts" \
  > "$OMNI_DATA/server.log" 2>&1 &
SERVER_PID=$!

printf "waiting for the server on %s" "$PORT"
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/v1/health" >/dev/null 2>&1 || curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    break
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || { echo; echo "server died, see $OMNI_DATA/server.log" >&2; tail -20 "$OMNI_DATA/server.log" >&2; exit 1; }
  printf "."
  sleep 1
done
echo " up"

"$VENV/bin/omnigent" host --server "http://127.0.0.1:$PORT" \
  > "$OMNI_DATA/host.log" 2>&1 &
HOST_PID=$!
sleep 6

echo
echo "server  http://127.0.0.1:$PORT   log $OMNI_DATA/server.log"
echo "host    pid $HOST_PID            log $OMNI_DATA/host.log"
echo "harnesses the host reports as usable:"
curl -fsS "http://127.0.0.1:$PORT/v1/hosts" 2>/dev/null > "$OMNI_DATA/hosts.json" || true
python3 - "$OMNI_DATA/hosts.json" <<'PYEOF' || echo "  (could not read /v1/hosts)"
import json, sys
hosts = json.load(open(sys.argv[1])).get("hosts", [])
for h in hosts:
    ch = h.get("configured_harnesses") or {}
    usable = sorted(k for k, v in ch.items() if v is True)
    print("  %s: %s" % (h.get("name") or h.get("host_id"), ", ".join(usable)))
    for name in ("codex-native", "claude-native"):
        if name in ch and ch[name] is not True:
            print("    %s is not usable: %s" % (name, ch[name]))
PYEOF
echo
echo "leave this running. Ctrl+C stops both."
wait
