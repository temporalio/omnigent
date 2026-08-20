"""The answer waits for the sub-agents the turn delegated to.

Driven through a real ``OmnigentClient`` over a mock transport, so the request
shapes are the ones the server would see.
"""

from __future__ import annotations

import httpx
import pytest
from omnigent_client import OmnigentClient
from omnigent_temporal.activities import _PromptState, _settle_subtree
from omnigent_temporal.config import Config

MARKER = "​[omnigent-temporal:p1]"
SESSION = "conv_abc123"


def _config(**overrides: object) -> Config:
    base = {
        "address": "127.0.0.1:7233",
        "namespace": "default",
        "task_queue": "q",
        "server_url": "http://example.invalid",
        "agent": "echo",
        "workspace": "/tmp",
        "idle_timeout_seconds": 300,
        "turn_timeout_seconds": 1800,
        "recover_after_seconds": 60,
        "await_subtree": True,
        "subtree_timeout_seconds": 10,
    }
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def _items(answer: str) -> dict[str, object]:
    return {
        "data": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"go{MARKER}"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": answer}],
            },
        ]
    }


def _client(handler) -> tuple[OmnigentClient, httpx.MockTransport]:
    client = OmnigentClient("http://example.invalid")
    transport = httpx.MockTransport(handler)
    client._http._transport = transport
    return client, transport


@pytest.mark.asyncio
async def test_answer_waits_for_a_busy_child_and_takes_the_later_answer() -> None:
    # Busy for the first two checks, then quiet.
    busy_checks = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/child_sessions"):
            busy_checks["n"] += 1
            busy = busy_checks["n"] <= 2
            return httpx.Response(
                200, json={"data": [{"id": "child", "busy": busy}] if busy else []}
            )
        if request.url.path.endswith("/items"):
            # The parent speaks again once the child reports back.
            answer = "partial" if busy_checks["n"] <= 2 else "final"
            return httpx.Response(200, json=_items(answer))
        raise AssertionError(f"unexpected call: {request.url.path}")

    client, transport = _client(handler)
    try:
        answer = await _settle_subtree(
            client, SESSION, MARKER, _config(), _PromptState(True, True, "partial")
        )
    finally:
        await client.close()
        await transport.aclose()

    assert busy_checks["n"] >= 3
    assert answer == "final"


@pytest.mark.asyncio
async def test_a_quiet_subtree_costs_one_check() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/child_sessions"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json=_items("answer"))

    client, transport = _client(handler)
    try:
        answer = await _settle_subtree(
            client, SESSION, MARKER, _config(), _PromptState(True, True, "answer")
        )
    finally:
        await client.close()
        await transport.aclose()

    assert answer == "answer"
    assert sum(1 for p in calls if p.endswith("/child_sessions")) == 1


@pytest.mark.asyncio
async def test_a_wedged_child_does_not_hold_the_turn_open_forever() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/child_sessions"):
            return httpx.Response(200, json={"data": [{"id": "child", "busy": True}]})
        return httpx.Response(200, json=_items("answer"))

    client, transport = _client(handler)
    try:
        answer = await _settle_subtree(
            client,
            SESSION,
            MARKER,
            _config(subtree_timeout_seconds=4),
            _PromptState(True, True, "answer"),
        )
    finally:
        await client.close()
        await transport.aclose()

    assert answer == "answer"


@pytest.mark.asyncio
async def test_opting_out_skips_the_walk_entirely() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made")

    client, transport = _client(handler)
    try:
        answer = await _settle_subtree(
            client,
            SESSION,
            MARKER,
            _config(await_subtree=False),
            _PromptState(True, True, "answer"),
        )
    finally:
        await client.close()
        await transport.aclose()

    assert answer == "answer"
