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
exec "$PY" - "$KEY" "$PROMPT" <<'PY'
import asyncio
import sys
import uuid

from omnigent_temporal.client import session_state, submit_prompt

KEY, PROMPT = sys.argv[1], sys.argv[2]


async def main() -> int:
    prompt_id = str(uuid.uuid4())
    await submit_prompt(KEY, PROMPT, prompt_id)
    print(f"promptId {prompt_id}")
    print("waiting for the answer (Ctrl-C is safe; the turn keeps running)")

    for _ in range(900):
        state = await session_state(KEY)
        finished = state.finished
        # Match the prompt: `finished` still holds the previous turn until this
        # one lands, so waiting for "any finished turn" reports a stale answer.
        if finished is not None and finished.prompt_id == prompt_id:
            print(f"\noutcome: {finished.outcome}")
            print(f"session: {state.session_id}")
            if finished.final_text:
                print(f"\n{finished.final_text}")
            else:
                print("\n(no answer; check the worker log and the session's items)")
            return 0 if finished.outcome == "answered" else 1
        await asyncio.sleep(2)

    print("no answer yet; check the worker log", file=sys.stderr)
    return 1


raise SystemExit(asyncio.run(main()))
PY
