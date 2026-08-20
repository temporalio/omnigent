"""Names and shapes shared by the workflow, the client, and the activities.

Kept free of the Omnigent SDK and of anything that touches the clock, the
network or the filesystem: the workflow imports this inside Temporal's sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WORKFLOW_TYPE = "OmnigentSession"
WORKFLOW_ID_PREFIX = "omnigent-session-"

SUBMIT_PROMPT_SIGNAL = "submitPrompt"
INTERRUPT_SIGNAL = "interrupt"
SESSION_STATE_QUERY = "sessionState"


def workflow_id(key: str) -> str:
    return f"{WORKFLOW_ID_PREFIX}{key}"


@dataclass
class PromptInput:
    # Deterministic id for this prompt, so a re-driven activity can tell whether
    # it already reached the server.
    prompt_id: str
    text: str


@dataclass
class RunTurnInput:
    prompt_id: str
    text: str
    agent: str
    # Omnigent mints the session id when the session is created, so it is absent
    # on the first turn and known after. The workflow is what remembers it.
    session_id: str | None = None


@dataclass
class RunTurnResult:
    session_id: str
    final_text: str
    # Whether this call drove a turn, or found the prompt already answered and
    # read the answer back.
    ran: bool


@dataclass
class TurnRecord:
    prompt_id: str
    outcome: str  # "answered" | "interrupted" | "failed"
    final_text: str


@dataclass
class SessionState:
    """What the session is doing, for anyone watching from outside.

    The conversation itself lives in Omnigent's own store, not here.
    """

    queued: int = 0
    session_id: str | None = None
    running: str | None = None
    finished: TurnRecord | None = None


@dataclass
class SessionOptions:
    # How long the workflow stays alive with no work before it retires. The next
    # prompt starts a fresh run, which rebuilds nothing: Omnigent's store
    # already holds the conversation.
    idle_timeout_seconds: int = 300
    labels: dict[str, str] = field(default_factory=dict)
