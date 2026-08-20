#!/usr/bin/env bash
# Ask the agent something and wait for the answer.
#
#   dev/ask.sh "Reply with the single word HELLO."
#   dev/ask.sh my-session "and what did you just say?"   # same session twice
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
need_venv

if [ $# -ge 2 ]; then
  KEY="$1"; shift
else
  KEY="dev-$(date +%s)"
fi
PROMPT="${1:?usage: dev/ask.sh [session-key] \"your prompt\"}"

log "session '$KEY'"
"$PY" -m omnigent_temporal submit "$KEY" "$PROMPT"

log "waiting for the answer (Ctrl-C is safe; the turn keeps running)"
exec "$PY" - "$KEY" <<'PY'
import asyncio, sys
from omnigent_temporal.client import session_state

async def main(key: str) -> int:
    for _ in range(360):
        state = await session_state(key)
        if state.finished:
            print(f"\noutcome: {state.finished.outcome}")
            print(f"session: {state.session_id}")
            print(f"\n{state.finished.final_text}")
            return 0 if state.finished.outcome == "answered" else 1
        await asyncio.sleep(2)
    print("no answer yet; check the worker log", file=sys.stderr)
    return 1

raise SystemExit(asyncio.run(main(sys.argv[1])))
PY
