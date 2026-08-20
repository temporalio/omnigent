"""Client helpers: submit a prompt to a session, read what it is doing, and stop
it.

The prompt rides a signal-with-start, so a first prompt starts the per-session
workflow and later prompts coalesce into the running one.
"""

from __future__ import annotations

import uuid

from temporalio.client import Client

from .config import from_env
from .protocol import (
    INTERRUPT_SIGNAL,
    SESSION_STATE_QUERY,
    SUBMIT_PROMPT_SIGNAL,
    PromptInput,
    SessionOptions,
    SessionState,
    workflow_id,
)


async def _connect() -> tuple[Client, object]:
    cfg = from_env()
    client = await Client.connect(cfg.address, namespace=cfg.namespace)
    return client, cfg


async def submit_prompt(key: str, text: str, prompt_id: str | None = None) -> str:
    prompt_id = prompt_id or str(uuid.uuid4())
    client, cfg = await _connect()
    await client.start_workflow(
        "OmnigentSession",
        args=[cfg.agent, SessionOptions(idle_timeout_seconds=cfg.idle_timeout_seconds)],
        id=workflow_id(key),
        task_queue=cfg.task_queue,
        start_signal=SUBMIT_PROMPT_SIGNAL,
        start_signal_args=[PromptInput(prompt_id=prompt_id, text=text)],
    )
    return prompt_id


async def session_state(key: str) -> SessionState:
    client, _ = await _connect()
    handle = client.get_workflow_handle(workflow_id(key))
    return await handle.query(SESSION_STATE_QUERY, result_type=SessionState)


async def interrupt(key: str) -> None:
    client, _ = await _connect()
    handle = client.get_workflow_handle(workflow_id(key))
    await handle.signal(INTERRUPT_SIGNAL)
