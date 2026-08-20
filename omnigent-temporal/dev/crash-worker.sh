#!/usr/bin/env bash
# Kill the worker mid-turn and watch the turn finish anyway.
#
# The turn does not run in the worker: Omnigent's server drives it on a runner.
# So this reads the answer out of Omnigent's own log with no worker running at
# all. Restart the worker afterwards and the retried activity finds the answer
# already there rather than asking the same thing twice.
#
# Needs temporal.sh, omnigent.sh and worker.sh running.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
need_venv

WORKER_PID="$(pid_of worker)" || die "no worker recorded. Start dev/worker.sh"

KEY="crash-worker-$(date +%s)"
log "session '$KEY'"

# The worker pid, not a pattern: a pattern would also take out a worker someone
# else on this machine is running.
WORKER_PID="$WORKER_PID" "$PY" - "$KEY" <<'PY'
import asyncio, os, signal, sys, uuid

from omnigent_client import OmnigentClient
from omnigent_temporal.activities import _marker, _prompt_state
from omnigent_temporal.client import submit_prompt
from omnigent_temporal.config import from_env

KEY = sys.argv[1]
PROMPT = "Count from 1 to 300, one number per line, then on the last line write SURVIVED."


async def find_session(client: OmnigentClient, marker: str):
    """The workflow only learns the session id once the turn succeeds, so look for
    our own prompt in the sessions the server holds."""
    for _ in range(120):
        for row in await client.sessions.list(limit=20):
            state = await _prompt_state(client, row.id, marker)
            if state.recorded:
                return row.id, state
        await asyncio.sleep(1)
    return None, None


async def main() -> int:
    cfg = from_env()
    prompt_id = str(uuid.uuid4())
    marker = _marker(prompt_id)
    await submit_prompt(KEY, PROMPT, prompt_id)

    async with OmnigentClient(base_url=cfg.server_url) as client:
        session_id, state = await find_session(client, marker)
        if session_id is None:
            print("the prompt never reached the server", file=sys.stderr)
            return 1
        print(f"session {session_id}: prompt recorded, answered={state.answered}")
        if state.answered:
            print("it finished before we could interrupt; try a longer prompt", file=sys.stderr)
            return 1

        worker_pid = int(os.environ["WORKER_PID"])
        print(f"killing the worker (pid {worker_pid})")
        os.kill(worker_pid, signal.SIGKILL)
        print("no worker is running now. Waiting on Omnigent's own log.")

        for _ in range(300):
            state = await _prompt_state(client, session_id, marker)
            if state.answered:
                items = await client.sessions.list_items(session_id, limit=1000, order="asc")

                def text(item):
                    return "".join(
                        b.get("text", "") for b in item.get("content", []) if isinstance(b, dict)
                    )

                marked = sum(
                    1
                    for i in items
                    if i.get("type") == "message"
                    and i.get("role") == "user"
                    and marker in text(i)
                )
                print(f"\nthe turn finished with no worker running: ...{state.final_text[-30:]!r}")
                print(f"copies of the prompt in the log: {marked} (1 means it was not asked twice)")
                print("\nRestart dev/worker.sh: the retried activity reads this answer back.")
                return 0 if marked == 1 else 1
            await asyncio.sleep(2)

    print("timed out", file=sys.stderr)
    return 1


raise SystemExit(asyncio.run(main()))
PY
