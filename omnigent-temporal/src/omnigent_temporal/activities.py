"""The durable step body: drive one Omnigent turn, with Omnigent's own store as
the log.

Omnigent runs the turn on its server, not here: ``post_event`` is a 202 and the
server drives the agent detached from whoever asked. So this activity submits
once and then watches. That split is why a worker dying is cheap here, and it is
also why the durability boundary sits at the server rather than at this process.
"""

from __future__ import annotations

import asyncio
import contextlib
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


def beat(details: object) -> None:
    """Heartbeat, tolerating being called outside an activity.

    The polling helpers are exercised directly in tests, where there is no
    activity context to report to.
    """
    with contextlib.suppress(RuntimeError):
        activity.heartbeat(details)


class _PromptState:
    """What the item log says about one prompt."""

    def __init__(self, recorded: bool, answered: bool, final_text: str) -> None:
        self.recorded = recorded
        self.answered = answered
        self.final_text = final_text


async def _prompt_state(client: OmnigentClient, session_id: str, marker: str) -> _PromptState:
    items = await client.sessions.list_items(session_id, limit=1000, order="asc")
    return _scan(items, marker)


def _scan(items: list[dict], marker: str) -> _PromptState:
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
                beat(session_id)

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
            final_text = await _await_answer(client, session_id, marker, cfg)
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
    cfg: Config,
) -> str:
    """Poll until the turn has an answer, or we give up on it.

    Status alone is not the done-signal: a session reads ``idle`` in the moment
    between the prompt landing and the turn starting, and a parent reads ``idle``
    while its sub-agents still work. So the answer in the log is what we wait
    for.

    A turn that stopped making progress, or whose session went ``failed``, is
    asked to recover. Omnigent re-drives an interrupted turn when a runner
    connects, but nothing re-drives one whose runner died and stayed dead: the
    server has no startup sweep, and a later message drives its own turn rather
    than finishing this one. Asking here is what closes that gap, and it is cheap
    when the runner is healthy because the server reports ``already_connected``
    and does nothing.
    """
    waited = 0.0
    since_progress = 0.0
    since_recovery = float(cfg.recover_after_seconds)
    seen_items = -1

    while waited < cfg.turn_timeout_seconds:
        beat(session_id)
        items = await client.sessions.list_items(session_id, limit=1000, order="asc")
        state = _scan(items, marker)
        if state.answered:
            return await _settle_subtree(client, session_id, marker, cfg, state)

        # Any new item is progress, so only a genuinely stuck turn is recovered.
        if len(items) != seen_items:
            seen_items = len(items)
            since_progress = 0.0

        session = await client.sessions.get(session_id)
        failed = session.status == "failed"

        # A failed session is not a reason to stop: losing the runner is exactly
        # what recovery is for. Only give up when recovery itself cannot happen.
        if (failed or since_progress >= cfg.recover_after_seconds) and (
            since_recovery >= cfg.recover_after_seconds
        ):
            since_recovery = 0.0
            since_progress = 0.0
            try:
                ack = await client.sessions.retry_session(session_id)
            except Exception as exc:
                if failed:
                    raise RuntimeError(
                        f"omnigent session {session_id} failed and could not be recovered: {exc}"
                    ) from exc
                _logger.warning("recovery for %s was refused: %s", session_id, exc)
            else:
                _logger.info("asked %s to recover: %s", session_id, ack.get("recovery"))

        await asyncio.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS
        since_progress += _POLL_SECONDS
        since_recovery += _POLL_SECONDS

    raise TimeoutError(f"turn on {session_id} produced no answer in {cfg.turn_timeout_seconds}s")


async def _settle_subtree(
    client: OmnigentClient,
    session_id: str,
    marker: str,
    cfg: Config,
    answered: _PromptState,
) -> str:
    """Hold the turn open while the sub-agents it delegated to are still working.

    A parent reads answered the moment it speaks, and its own status reads idle
    once it has delegated, so an answer is not on its own proof the work is
    done. ``subtree_busy`` is the rollup that walks the descendants, and it is
    what the CLI badge and the web panel use.

    Point-in-time, so a child that has not spawned yet reads quiet: this closes
    the common case, not a race with a child being created.
    """
    if not cfg.await_subtree:
        return answered.final_text

    waited = 0.0
    while waited < cfg.subtree_timeout_seconds:
        beat(session_id)
        if not await client.sessions.subtree_busy(session_id):
            break
        await asyncio.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS
    else:
        _logger.warning(
            "sub-agents under %s were still busy after %ss; answering anyway",
            session_id,
            cfg.subtree_timeout_seconds,
        )

    # The parent often speaks again once its children report back, so the answer
    # worth returning is the one that stands after they are quiet.
    latest = await _prompt_state(client, session_id, marker)
    return latest.final_text or answered.final_text
