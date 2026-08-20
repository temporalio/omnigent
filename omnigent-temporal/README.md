# omnigent-temporal

A Temporal-backed durable executor for [Omnigent](https://github.com/omnigent-ai/omnigent) sessions. Same pattern we proved on the OpenCode fork, on Pi, and on Codex: the agent's own loop runs under a durable executor, while the session record stays in the app's own store.

Status: verified end to end against a live Omnigent server driving a `claude-native` agent. Two turns on one session work, and a worker killed mid-turn recovers with the answer and no duplicate prompt.

## The idea

Two parts of the state, two systems:

- **Durable storage** stays with Omnigent. The server persists every item to its conversation store before it forwards anything to a runner, and reads it back on reconnect. That store is the source of truth. We do not move it into Temporal.
- **Durable execution** comes from Temporal. A per-session workflow owns the prompt queue and the session id Omnigent minted, so turns are sequenced, a crash re-drives the turn in flight, and an operator can see what a session is doing.

## Why this one is different

On Pi and Codex the turn ran *inside* the process we drove, so killing the driver killed the turn. Omnigent is not like that: `POST /v1/sessions/{id}/events` returns 202 and the **server** drives the turn on a separate runner process, detached from whoever asked.

That changes what the activity is for. It submits once, then watches. A worker dying is cheap, because the turn keeps going without it and the retry just re-attaches.

It also moves the durability question one layer down, which is worth stating plainly rather than claiming more than we have.

## Where the boundary actually is

We read the server to find out what it guarantees on its own. It has no startup reconcile: the FastAPI lifespan never scans the store for in-flight turns, and the one recovery-adjacent comment says so outright. The crash-recovery logic lives in the **runner**, in its session-init handler, and it fires when a runner tunnel reconnects.

So:

- **Driver (this worker) dies:** the turn is unaffected. It runs on the server's runner and finishes. Temporal re-drives the activity, which re-attaches and reads the answer. Verified.
- **Runner survives, server restarts:** the runner reconnects (it retries forever with backoff), the server re-inits each of its sessions, and recovery is a backstop. The turn finishes.
- **Runner dies and stays dead:** nothing in Omnigent re-drives the interrupted turn. It sits abandoned until the next user message arrives, and that path suppresses recovery, so the new message drives its own turn rather than the old one completing.

The executor closes that last case itself. A turn that stops producing items, or whose session goes `failed`, is asked to recover, and Omnigent already ships the mechanism: a `retry_session` event relaunches the runner through the host and initializes the session with recovery enabled, so the interrupted turn is driven to its end. It adds nothing to the transcript, so the prompt is not asked twice, and a healthy runner makes it a no-op (`already_connected`), which is what makes it safe to call on any stall.

Recovery is only meaningful off the native path. For `claude-native` and friends the vendor CLI owns the turn, so relaunching gets the terminal back (`native_terminal_ready`) without resuming what was interrupted.

## Idempotency

Omnigent generates item ids server-side (`turn_{uuid4}`) and `SessionEventInput` carries no client key, so a re-driven activity cannot ask the server "did you already get this?". It has to ask the log.

So the prompt carries a zero-width marker with its prompt id, and on a retry the activity reads the item list:

- Marker absent: post the prompt.
- Marker present with an answer after it: the turn finished but the result never reached Temporal. Read the answer back, run nothing.
- Marker present with no answer: a previous attempt already submitted and the server is still working. Wait, do not post again.

The marker also tells our prompts apart from the context Omnigent injects as user-role messages of its own. Interestingly the codex-native path inside Omnigent dedupes the same way, by matching prompt text.

An answer is not on its own the end of the turn. A parent that delegates reads answered the moment it speaks, and its own status reads idle once the work is with its children, so the executor holds the turn open while `subtree_busy` reports descendants still working, then takes whatever answer stands after they are quiet (a parent usually speaks again once its children report back). It is bounded by `OMNIGENT_SUBTREE_TIMEOUT_SECONDS` so one wedged child cannot hold a turn open forever, costs a single call when there are no sub-agents, and `OMNIGENT_AWAIT_SUBTREE=0` turns it off. It is point-in-time, so a child that has not spawned yet reads quiet: this closes the common case, not a race with a child being created.

Waiting keys on the **item log**, not on `status`. A session reads `idle` in the gap between a prompt landing and the turn starting, and a parent reads `idle` while its sub-agents still work, so status alone would report a turn done that never ran. Status is used only to notice failure.

## What this fork adds

The SDK could not create a session that runs on a registered host: `create_from_agent_id` took `workspace` but no `host_id`, and a host-launched runner needs both. Without a host, a headless client's first turn fails with `runner_failed_to_start`.

- `create_from_agent_id(..., host_id=...)` passes the host through.
- `resolve_online_host(harness=...)` finds an online host that has the harness configured, the counterpart to the existing `resolve_online_runner` for the other topology.
- `retry_session(session_id)` posts the `retry_session` event. The server already implemented it; the SDK just did not expose it.

All three are small, and all three are what a driver with no runner of its own needs.

## Running it

```
uv venv --python 3.12 && uv pip install temporalio -e ../sdks/python-client -e .
omnigent server --host 127.0.0.1 --port 8791          # in one shell
omnigent host --server http://127.0.0.1:8791          # in another: provides the runner
python -m omnigent_temporal.worker                    # in a third
python -m omnigent_temporal submit my-key "Reply with the single word FOXTROT."
python -m omnigent_temporal state my-key
python -m omnigent_temporal interrupt my-key
```

Env: `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `OMNIGENT_TEMPORAL_TASK_QUEUE`, `OMNIGENT_SERVER_URL`, `OMNIGENT_AGENT`, `OMNIGENT_WORKSPACE`, `OMNIGENT_IDLE_TIMEOUT_SECONDS`, `OMNIGENT_TURN_TIMEOUT_SECONDS`, `OMNIGENT_RECOVER_AFTER_SECONDS`, `OMNIGENT_AWAIT_SUBTREE`, `OMNIGENT_SUBTREE_TIMEOUT_SECONDS`.

`temporal workflow query --workflow-id omnigent-session-<key> --name sessionState` reports the queue, the session id, the turn in flight, and how the last turn ended.

Note: the repo's `uv.toml` uses a duration syntax older uv builds cannot parse, so `UV_NO_CONFIG=1` may be needed for uv commands run from inside this directory.

## Checks

`tests/` covers the sub-agent gate against a mock transport: an answer waits for a busy child and takes the later one, a quiet subtree costs one check, a wedged child does not hold the turn open forever, and opting out skips the walk. Run it with `python -m pytest tests`.

`loop_check.py` runs the workflow against a real Temporal server with the turn activity stubbed, so it needs no Omnigent server and no model key: one activity per prompt, the session id carried into the second turn, an interrupt that reaches the server and still lets the session serve a later prompt.

Two crash tests, for the two things that can die.

**The worker dies.** Submit a turn whose shell command sleeps, wait until the marked prompt is in the item log with no answer, `pkill -9 -f omnigent_temporal.worker`, wait past the heartbeat timeout, start a fresh worker. The observed run went from (1 marked prompt, no answer) to (1 marked prompt, answered GOLF), with the activity completing on attempt 2. The turn itself was never in danger: it ran on the server throughout.

**The runner dies.** Submit a long turn against a non-native agent, then `pkill -9 -f omnigent.runner._zygote`. Nothing in Omnigent would finish that turn. The observed run recovered it: the worker logged `asked <session> to recover: runner_relaunched`, the answer came back complete (200 lines then JULIET), the item log held exactly one copy of the prompt, and the activity completed on **attempt 1** — the worker never restarted, so it was the turn that was recovered, not the driver.

## Layout

- `src/omnigent_temporal/config.py` — Temporal + Omnigent wiring from env.
- `src/omnigent_temporal/protocol.py` — workflow id, signal and query names, shared shapes.
- `src/omnigent_temporal/activities.py` — `run_turn`: submit once, then watch; `interrupt_session`.
- `src/omnigent_temporal/workflow.py` — `OmnigentSession`: per-session executor (submit, drive, interrupt, idle-retire).
- `src/omnigent_temporal/worker.py` — worker hosting the workflow and the activities.
- `src/omnigent_temporal/client.py` — submit a prompt, read state, interrupt.

## Known gaps

- **No step level, and not for the reason you would guess.** A turn is the smallest unit Omnigent can *drive*. It is not that the loop is expensive to split: Omnigent has no loop of its own to split. Every shipped executor reports `handles_tools_internally() == True`, so the model-call-then-tools loop always lives in the vendor SDK or the vendor CLI. Omnigent brokers turns; it never sits between "the model asked for a tool" and "the tool ran". A step is something it can observe, not something it can gate.

  Two things soften that. Step boundaries are already persisted per item, so this executor can checkpoint *on* steps without any fork. And a stepwise protocol does exist in the tree, orphaned: `TurnComplete(continue_turn=True)` plus `max_turns=1` and an in-memory resume state in the openai-agents executor, with zero consumers repo-wide. Reviving it would buy step gating for one harness, with the resume state still in process memory, so a crash between steps would fall back to full-history replay anyway.
- **No orphan tool-call repair to rely on.** Omnigent does not synthesize a placeholder tool output: native harnesses leave it to the vendor CLI's own resume, and the SDK path drops tool items when rebuilding the prompt.
