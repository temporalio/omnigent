"""Checks the executor's shape against a real Temporal server with the turn
activity stubbed: one activity per prompt, the session id remembered across
turns, and an interrupt that ends the turn without ending the session.

Needs a Temporal server, no Omnigent server and no model key.

    python loop_check.py
"""

from __future__ import annotations

import asyncio
import os
import uuid

from omnigent_temporal.protocol import (
    INTERRUPT_SIGNAL,
    SESSION_STATE_QUERY,
    SUBMIT_PROMPT_SIGNAL,
    PromptInput,
    RunTurnInput,
    RunTurnResult,
    SessionOptions,
    SessionState,
    workflow_id,
)
from omnigent_temporal.workflow import OmnigentSession
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")
TASK_QUEUE = f"omnigent-loop-check-{uuid.uuid4().hex[:8]}"

seen: dict[str, list[str | None]] = {}
interrupted_sessions: list[str] = []


@activity.defn(name="run_turn")
async def run_turn(inp: RunTurnInput) -> RunTurnResult:
    seen.setdefault(inp.prompt_id, []).append(inp.session_id)
    if inp.text == "hang":
        await asyncio.sleep(600)
    # Stand in for the id Omnigent would mint on the first turn.
    return RunTurnResult(session_id=inp.session_id or "conv_stub", final_text="ok", ran=True)


@activity.defn(name="interrupt_session")
async def interrupt_session(session_id: str) -> None:
    interrupted_sessions.append(session_id)


async def wait_for(what: str, predicate, timeout: float = 30.0) -> None:
    waited = 0.0
    while waited < timeout:
        if predicate():
            return
        await asyncio.sleep(0.2)
        waited += 0.2
    raise AssertionError(f"timed out waiting for {what}")


async def main() -> int:
    client = await Client.connect(ADDRESS)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[OmnigentSession],
        activities=[run_turn, interrupt_session],
    )

    failures: list[str] = []

    def check(what: str, ok: bool, detail: object = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'} {what}" + ("" if ok else f" ({detail!r})"))
        if not ok:
            failures.append(what)

    async with worker:
        key = f"loop-check-{uuid.uuid4().hex[:8]}"
        options = SessionOptions(idle_timeout_seconds=10)

        async def send(prompt: PromptInput) -> None:
            await client.start_workflow(
                "OmnigentSession",
                args=["stub-agent", options],
                id=workflow_id(key),
                task_queue=TASK_QUEUE,
                start_signal=SUBMIT_PROMPT_SIGNAL,
                start_signal_args=[prompt],
            )

        handle = client.get_workflow_handle(workflow_id(key))

        first = PromptInput(prompt_id=str(uuid.uuid4()), text="one")
        await send(first)
        await wait_for("the first turn", lambda: first.prompt_id in seen)
        await asyncio.sleep(1.0)
        check("the first turn ran once", seen[first.prompt_id] == [None], seen[first.prompt_id])

        # The workflow is what remembers the session id, so turn two carries it.
        second = PromptInput(prompt_id=str(uuid.uuid4()), text="two")
        await send(second)
        await wait_for("the second turn", lambda: second.prompt_id in seen)
        await asyncio.sleep(1.0)
        check(
            "the second turn resumed the same session",
            seen[second.prompt_id] == ["conv_stub"],
            seen[second.prompt_id],
        )

        # An interrupt ends the turn, and reaches the server.
        hanging = PromptInput(prompt_id=str(uuid.uuid4()), text="hang")
        await send(hanging)
        await wait_for("the hanging turn to start", lambda: hanging.prompt_id in seen)
        await handle.signal(INTERRUPT_SIGNAL)
        await wait_for("the server to be told to stop", lambda: interrupted_sessions != [])
        check(
            "the interrupt reached the server",
            interrupted_sessions == ["conv_stub"],
            interrupted_sessions,
        )

        state = await handle.query(SESSION_STATE_QUERY, result_type=SessionState)
        check(
            "the interrupt was recorded as the outcome",
            state.finished.outcome == "interrupted",
            state.finished,
        )

        # The session still serves a later prompt.
        after = PromptInput(prompt_id=str(uuid.uuid4()), text="three")
        await send(after)
        await wait_for("a later prompt", lambda: after.prompt_id in seen)
        await asyncio.sleep(1.0)
        state = await handle.query(SESSION_STATE_QUERY, result_type=SessionState)
        check(
            "the session survived the interrupt",
            state.finished.outcome == "answered",
            state.finished,
        )

        await handle.terminate("loop check done")

    print("loop_check: OK" if not failures else f"loop_check: {len(failures)} failed")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
