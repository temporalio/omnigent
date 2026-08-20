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
- **Runner dies with the server, and nothing persistent replaces it:** nothing re-drives the interrupted turn. It sits abandoned until the next user message arrives, and that path suppresses recovery, so the new message drives the turn rather than the old one completing.

That last case is the real gap, and it is why a durable executor in front is worth having rather than trusting the server alone. Closing it properly means either guaranteeing a persistent runner, having Temporal re-drive the turn itself, or adding a server-startup reconcile. The third is a fork change we have not made.

## Idempotency

Omnigent generates item ids server-side (`turn_{uuid4}`) and `SessionEventInput` carries no client key, so a re-driven activity cannot ask the server "did you already get this?". It has to ask the log.

So the prompt carries a zero-width marker with its prompt id, and on a retry the activity reads the item list:

- Marker absent: post the prompt.
- Marker present with an answer after it: the turn finished but the result never reached Temporal. Read the answer back, run nothing.
- Marker present with no answer: a previous attempt already submitted and the server is still working. Wait, do not post again.

The marker also tells our prompts apart from the context Omnigent injects as user-role messages of its own. Interestingly the codex-native path inside Omnigent dedupes the same way, by matching prompt text.

Waiting keys on the **item log**, not on `status`. A session reads `idle` in the gap between a prompt landing and the turn starting, and a parent reads `idle` while its sub-agents still work, so status alone would report a turn done that never ran. Status is used only to notice failure.

## What this fork adds

The SDK could not create a session that runs on a registered host: `create_from_agent_id` took `workspace` but no `host_id`, and a host-launched runner needs both. Without a host, a headless client's first turn fails with `runner_failed_to_start`.

- `create_from_agent_id(..., host_id=...)` passes the host through.
- `resolve_online_host(harness=...)` finds an online host that has the harness configured, the counterpart to the existing `resolve_online_runner` for the other topology.

Both are small, and both are what a driver with no runner of its own needs.

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

Env: `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `OMNIGENT_TEMPORAL_TASK_QUEUE`, `OMNIGENT_SERVER_URL`, `OMNIGENT_AGENT`, `OMNIGENT_WORKSPACE`, `OMNIGENT_IDLE_TIMEOUT_SECONDS`, `OMNIGENT_TURN_TIMEOUT_SECONDS`.

`temporal workflow query --workflow-id omnigent-session-<key> --name sessionState` reports the queue, the session id, the turn in flight, and how the last turn ended.

Note: the repo's `uv.toml` uses a duration syntax older uv builds cannot parse, so `UV_NO_CONFIG=1` may be needed for uv commands run from inside this directory.

## Checks

`loop_check.py` runs the workflow against a real Temporal server with the turn activity stubbed, so it needs no Omnigent server and no model key: one activity per prompt, the session id carried into the second turn, an interrupt that reaches the server and still lets the session serve a later prompt.

The crash test: submit a turn whose shell command sleeps, wait until the marked prompt is in the item log with no answer, `pkill -9 -f omnigent_temporal.worker`, wait past the heartbeat timeout, start a fresh worker. The observed run went from (1 marked prompt, no answer) to (1 marked prompt, answered GOLF) with the activity completing on attempt 2.

## Layout

- `src/omnigent_temporal/config.py` — Temporal + Omnigent wiring from env.
- `src/omnigent_temporal/protocol.py` — workflow id, signal and query names, shared shapes.
- `src/omnigent_temporal/activities.py` — `run_turn`: submit once, then watch; `interrupt_session`.
- `src/omnigent_temporal/workflow.py` — `OmnigentSession`: per-session executor (submit, drive, interrupt, idle-retire).
- `src/omnigent_temporal/worker.py` — worker hosting the workflow and the activities.
- `src/omnigent_temporal/client.py` — submit a prompt, read state, interrupt.

## Known gaps

- **No step level.** A turn is the smallest durable unit. Omnigent exposes no "run one model call and its tools, then stop", so a crash re-drives from wherever the store left off. On the Pi fork we added stepping; here it would be a runner change.
- **A server restart with no surviving runner abandons the turn.** See above. Temporal notices (the activity keeps waiting and eventually times out) but does not currently re-post the prompt, because re-posting is the one thing that would duplicate work.
- **Sub-agents are not tracked.** A parent turn that delegates reads answered when the parent answers. `subtree_busy` exists and would be the way to gate on descendant work.
- **No orphan tool-call repair to rely on.** Omnigent does not synthesize a placeholder tool output: native harnesses leave it to the vendor CLI's own resume, and the SDK path drops tool items when rebuilding the prompt.
