"""The durable step body: drive one Omnigent turn, with Omnigent's own store as
the log.

Omnigent runs the turn on its server, not here: ``post_event`` is a 202 and the
server drives the agent detached from whoever asked. So this activity submits
once and then watches. That split is why a worker dying is cheap here, and it is
also why the durability boundary sits at the server rather than at this process.
"""

from __future__ import annotations

import asyncio
import logging

from omnigent_client import OmnigentClient
from temporalio import activity

from .config import Config
from .protocol import RunTurnInput, RunTurnResult

_logger = logging.getLogger(__name__)

_POLL_SECONDS = 2.0

# The prompt carries a zero-width marker with its prompt id, so a re-driven
# activity can tell whether this exact prompt already reached the server. It also
# tells our prompts apart from context Omnigent injects as user messages.
def _marker(prompt_id: str) -> str:
    return f"​[omnigent-temporal:{prompt_id}]"


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


class _PromptState:
    """What the item log says about one prompt."""

    def __init__(self, recorded: bool, answered: bool, final_text: str) -> None:
        self.recorded = recorded
        self.answered = answered
        self.final_text = final_text


async def _prompt_state(client: OmnigentClient, session_id: str, marker: str) -> _PromptState:
    items = await client.sessions.list_items(session_id, limit=1000, order="asc")
    recorded = False
    answered = False
    final_text = ""
    for item in items:
        if item.get("type") != "message":
            continue
        role = item.get("role")
        text = _text_of(item.get("content"))
        if not recorded:
            if role == "user" and marker in text:
                recorded = True
            continue
        # An assistant message that Omnigent itself flagged as interrupted is a
        # partial, so it is not an answer.
        if role == "assistant" and not item.get("interrupted") and text.strip():
            answered = True
            final_text = text
    return _PromptState(recorded, answered, final_text)


def _session_id_from_last_attempt() -> str | None:
    """The session id heartbeated by a previous attempt.

    Omnigent mints it when the session is created, so this is how a retry of a
    first turn learns an id the workflow never got to hear about.
    """
    details = activity.info().heartbeat_details
    if not details:
        return None
    last = details[-1]
    return last if isinstance(last, str) and last else None


def make_activities(cfg: Config):
    @activity.defn(name="run_turn")
    async def run_turn(inp: RunTurnInput) -> RunTurnResult:
        marker = _marker(inp.prompt_id)
        async with OmnigentClient(base_url=cfg.server_url) as client:
            session_id = inp.session_id or _session_id_from_last_attempt()

            if session_id is None:
                agent = await client.sessions.resolve_agent(inp.agent)
                # A worker has no runner of its own, so the session has to name a
                # host that can launch one. Without it the first turn fails with
                # runner_failed_to_start.
                host_id = await client.sessions.resolve_online_host(harness=agent.harness)
                if host_id is None:
                    raise RuntimeError(
                        f"no online omnigent host offers the {agent.harness} harness"
                    )
                session = await client.sessions.create_from_agent_id(
                    agent.id,
                    host_id=host_id,
                    workspace=cfg.workspace,
                )
                session_id = session.id
                # Heartbeat the id the moment it exists, so a crash from here on
                # resumes this session instead of opening a second one.
                activity.heartbeat(session_id)

            state = await _prompt_state(client, session_id, marker)
            if state.answered:
                # A retry that landed after the turn finished but before its
                # result reached Temporal. The answer is already in the log.
                return RunTurnResult(session_id=session_id, final_text=state.final_text, ran=False)

            if not state.recorded:
                await client.sessions.post_event(
                    session_id,
                    {
                        "type": "message",
                        "data": {
                            "role": "user",
                            "content": [{"type": "input_text", "text": f"{inp.text}{marker}"}],
                        },
                    },
                )

            # Either we just submitted, or a previous attempt did and the server
            # is still working on it. Both end the same way: wait for the answer.
            final_text = await _await_answer(client, session_id, marker, cfg.turn_timeout_seconds)
            return RunTurnResult(session_id=session_id, final_text=final_text, ran=True)

    @activity.defn(name="interrupt_session")
    async def interrupt_session(session_id: str) -> None:
        """Stop a turn on the server.

        Cancelling the activity is not enough on its own: the server owns the
        running turn, so it keeps going unless it is told to stop.
        """
        async with OmnigentClient(base_url=cfg.server_url) as client:
            await client.sessions.interrupt(session_id)

    return [run_turn, interrupt_session]


async def _await_answer(
    client: OmnigentClient,
    session_id: str,
    marker: str,
    timeout_seconds: int,
) -> str:
    """Poll until the turn has an answer, the session fails, or we give up.

    Status alone is not enough: a session reads ``idle`` in the moment between
    the prompt landing and the turn starting, and a parent reads ``idle`` while
    its sub-agents still work. So the answer in the log is the signal, and status
    is only used to notice failure.
    """
    waited = 0.0
    while waited < timeout_seconds:
        activity.heartbeat(session_id)
        state = await _prompt_state(client, session_id, marker)
        if state.answered:
            return state.final_text

        session = await client.sessions.get(session_id)
        if session.status == "failed":
            raise RuntimeError(f"omnigent session {session_id} failed")

        await asyncio.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS

    raise TimeoutError(f"turn on {session_id} produced no answer in {timeout_seconds}s")
