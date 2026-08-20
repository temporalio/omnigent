"""Temporal + Omnigent wiring, read from env.

The workflow never reads this: workflow code has to stay deterministic. The
client and the worker do.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    address: str
    namespace: str
    task_queue: str
    # The Omnigent server this drives. Its own store is the durable record, so a
    # fleet of workers points at one server (or a replicated one).
    server_url: str
    agent: str
    # Absolute path on the host the agent works in. The server requires it
    # whenever a host launches the runner.
    workspace: str
    idle_timeout_seconds: int
    # How long to wait for one turn to finish before giving up on it.
    turn_timeout_seconds: int
    # How long a turn may show no progress before we ask the server to bring its
    # runner back. A turn whose runner died is not re-driven by anything else.
    recover_after_seconds: int
    # Whether an answer waits for the sub-agents it delegated to. A parent reads
    # answered as soon as it speaks, which is too early when its children are
    # still working.
    await_subtree: bool
    # Bound on that wait, so one wedged child cannot hold a turn open forever.
    subtree_timeout_seconds: int


def from_env() -> Config:
    return Config(
        address=os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"),
        task_queue=os.environ.get("OMNIGENT_TEMPORAL_TASK_QUEUE", "omnigent-session"),
        server_url=os.environ.get("OMNIGENT_SERVER_URL", "http://127.0.0.1:8080"),
        agent=os.environ.get("OMNIGENT_AGENT", "claude-native-ui"),
        workspace=os.environ.get("OMNIGENT_WORKSPACE", os.getcwd()),
        idle_timeout_seconds=int(os.environ.get("OMNIGENT_IDLE_TIMEOUT_SECONDS", "300")),
        turn_timeout_seconds=int(os.environ.get("OMNIGENT_TURN_TIMEOUT_SECONDS", "1800")),
        recover_after_seconds=int(os.environ.get("OMNIGENT_RECOVER_AFTER_SECONDS", "60")),
        await_subtree=os.environ.get("OMNIGENT_AWAIT_SUBTREE", "1") != "0",
        subtree_timeout_seconds=int(os.environ.get("OMNIGENT_SUBTREE_TIMEOUT_SECONDS", "600")),
    )
