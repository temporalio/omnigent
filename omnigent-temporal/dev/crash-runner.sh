#!/usr/bin/env bash
# Kill the runner mid-turn and watch the executor bring the turn back.
#
# This is the case Omnigent does not recover on its own: no startup sweep, and a
# later message drives its own turn rather than finishing the interrupted one.
# The executor notices the stall and asks the server to relaunch the runner.
#
# Needs all three of temporal.sh, omnigent.sh, worker.sh running.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
need_venv

pgrep -f "omnigent_temporal.worker" >/dev/null || die "no worker running. Start dev/worker.sh"

# Recover sooner than the 60s default so the script does not look hung.
export OMNIGENT_RECOVER_AFTER_SECONDS="${OMNIGENT_RECOVER_AFTER_SECONDS:-25}"
log "note: the worker reads OMNIGENT_RECOVER_AFTER_SECONDS at startup, so set it there too for a faster demo"

KEY="crash-runner-$(date +%s)"
log "session '$KEY'"

exec "$PY" - "$KEY" <<'PY'
import asyncio, subprocess, sys, uuid
from omnigent_temporal.activities import _marker, _prompt_state
from omnigent_temporal.client import session_state, submit_prompt
from omnigent_temporal.config import from_env
from omnigent_client import OmnigentClient

KEY = sys.argv[1]
PROMPT = "Count from 1 to 300, one number per line, then on the last line write RECOVERED."

async def main() -> int:
    cfg = from_env()
    prompt_id = str(uuid.uuid4())
    marker = _marker(prompt_id)
    await submit_prompt(KEY, PROMPT, prompt_id)

    # Wait for the host to have a runner up, then take it out.
    for _ in range(120):
        found = subprocess.run(
            ["pgrep", "-f", "omnigent.runner._zygote"], capture_output=True, text=True
        )
        if found.stdout.strip():
            break
        await asyncio.sleep(1)
    else:
        print("no runner ever started", file=sys.stderr)
        return 1

    await asyncio.sleep(4)  # let the turn get going
    print("killing the runner")
    subprocess.run(["pkill", "-9", "-f", "omnigent.runner._zygote"], check=False)
    print("the runner is gone. Nothing in Omnigent would finish this turn on its own.")

    async with OmnigentClient(base_url=cfg.server_url) as client:
        for _ in range(600):
            st = await session_state(KEY)
            if st.finished:
                marked = 0
                if st.session_id:
                    items = await client.sessions.list_items(st.session_id, limit=1000, order="asc")
                    def text(item):
                        return "".join(
                            b.get("text", "") for b in item.get("content", []) if isinstance(b, dict)
                        )
                    marked = sum(
                        1 for i in items
                        if i.get("type") == "message" and i.get("role") == "user" and marker in text(i)
                    )
                print(f"\noutcome: {st.finished.outcome}")
                print(f"answer ends: ...{st.finished.final_text[-30:]!r}")
                print(f"copies of the prompt in the log: {marked} (1 means it was not asked twice)")
                print("\nLook for 'asked <session> to recover' in the worker log.")
                return 0 if st.finished.outcome == "answered" else 1
            await asyncio.sleep(2)
    print("timed out", file=sys.stderr)
    return 1

raise SystemExit(asyncio.run(main()))
PY
