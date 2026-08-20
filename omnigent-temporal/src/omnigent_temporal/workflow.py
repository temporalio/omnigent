"""The per-session durable executor. One workflow per Omnigent session.

It owns the small control state (the pending-prompt queue, and the session id
Omnigent minted); the conversation lives in Omnigent's own store, which the
run_turn activity reads and writes. A crash re-drives the turn in flight.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError

from .protocol import (
    INTERRUPT_SIGNAL,
    SESSION_STATE_QUERY,
    SUBMIT_PROMPT_SIGNAL,
    PromptInput,
    RunTurnInput,
    RunTurnResult,
    SessionOptions,
    SessionState,
    TurnRecord,
)


@workflow.defn(name="OmnigentSession")
class OmnigentSession:
    def __init__(self) -> None:
        self._queue: list[PromptInput] = []
        # Omnigent mints this when the session is created. Keeping it here is
        # what makes later turns land in the same session: the workflow is the
        # durable memory, not the worker.
        self._session_id: str | None = None
        self._running: str | None = None
        self._finished: TurnRecord | None = None
        self._interrupted = False

    @workflow.signal(name=SUBMIT_PROMPT_SIGNAL)
    def submit_prompt(self, prompt: PromptInput) -> None:
        self._queue.append(prompt)

    @workflow.signal(name=INTERRUPT_SIGNAL)
    def interrupt(self) -> None:
        self._interrupted = True

    @workflow.query(name=SESSION_STATE_QUERY)
    def session_state(self) -> SessionState:
        return SessionState(
            queued=len(self._queue),
            session_id=self._session_id,
            running=self._running,
            finished=self._finished,
        )

    @workflow.run
    async def run(self, agent: str, options: SessionOptions) -> None:
        idle = timedelta(seconds=options.idle_timeout_seconds)

        while True:
            woke = await workflow.wait_condition(lambda: bool(self._queue), timeout=idle)
            if not woke and not self._queue:
                # Idle: retire. The next prompt starts a fresh run, which
                # rebuilds nothing.
                return

            prompt = self._queue.pop(0)
            self._running = prompt.prompt_id
            self._interrupted = False
            outcome = "failed"
            final_text = ""

            turn = asyncio.create_task(
                workflow.execute_activity(
                    "run_turn",
                    RunTurnInput(
                        prompt_id=prompt.prompt_id,
                        text=prompt.text,
                        agent=agent,
                        session_id=self._session_id,
                    ),
                    result_type=RunTurnResult,
                    # A turn is a whole agent run (many model calls and tools),
                    # so give it room; the heartbeat is the real liveness bound
                    # and re-drives within seconds of a worker death.
                    start_to_close_timeout=timedelta(hours=2),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=100),
                )
            )
            stop = asyncio.create_task(workflow.wait_condition(lambda: self._interrupted))

            try:
                await workflow.wait([turn, stop], return_when=asyncio.FIRST_COMPLETED)
                if turn.done():
                    result: RunTurnResult = turn.result()
                    self._session_id = result.session_id
                    final_text = result.final_text
                    outcome = "answered"
                else:
                    # Tell the server to stop before dropping the activity, or
                    # the agent keeps working on a turn nobody is waiting for.
                    turn.cancel()
                    # A cancelled activity still has to be awaited, or its
                    # cancellation surfaces later as an unretrieved exception.
                    with contextlib.suppress(BaseException):
                        await turn
                    outcome = "interrupted"
                    if self._session_id is not None:
                        await workflow.execute_activity(
                            "interrupt_session",
                            self._session_id,
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
            except CancelledError:
                outcome = "interrupted"
            except ActivityError as exc:
                # The turn is recorded in Omnigent's store either way, so a
                # failure ends this turn rather than the session.
                workflow.logger.warning("turn %s failed: %s", prompt.prompt_id, exc)
            finally:
                stop.cancel()
                self._running = None
                self._finished = TurnRecord(
                    prompt_id=prompt.prompt_id,
                    outcome=outcome,
                    final_text=final_text,
                )
