"""Host selection tests for :class:`SessionsNamespace`.

A client with no runner of its own has to name a host that can launch one, so
these cover the host id reaching the create body and the online-host lookup.
"""

from __future__ import annotations

import json

import httpx
import pytest
from omnigent_client._client import OmnigentClient

_SESSION = {
    "id": "conv_abc123",
    "agent_id": "ag_abc123",
    "status": "idle",
    "created_at": 0,
    "items": [],
}


def _client(handler) -> tuple[OmnigentClient, httpx.MockTransport]:
    client = OmnigentClient("http://example.invalid")
    transport = httpx.MockTransport(handler)
    client._http._transport = transport
    return client, transport


@pytest.mark.asyncio
async def test_create_from_agent_id_sends_host_id() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(201, json=_SESSION)

    client, transport = _client(handler)
    try:
        await client.sessions.create_from_agent_id(
            "ag_abc123",
            host_id="host_abc123",
            workspace="/tmp/work",
        )
    finally:
        await client.close()
        await transport.aclose()

    assert bodies == [
        {"agent_id": "ag_abc123", "workspace": "/tmp/work", "host_id": "host_abc123"}
    ]


@pytest.mark.asyncio
async def test_create_from_agent_id_omits_host_id_when_absent() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(201, json=_SESSION)

    client, transport = _client(handler)
    try:
        await client.sessions.create_from_agent_id("ag_abc123")
    finally:
        await client.close()
        await transport.aclose()

    assert bodies == [{"agent_id": "ag_abc123"}]


@pytest.mark.asyncio
async def test_resolve_online_host_picks_a_host_with_the_harness_configured() -> None:
    listing = {
        "hosts": [
            {"host_id": "offline", "status": "offline", "configured_harnesses": {"claude-native": True}},
            # Configured but not ready reads as a reason, not as True.
            {"host_id": "needs-auth", "status": "online", "configured_harnesses": {"claude-native": "needs-auth"}},
            {"host_id": "ready", "status": "online", "configured_harnesses": {"claude-native": True}},
        ]
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=listing)

    client, transport = _client(handler)
    try:
        assert await client.sessions.resolve_online_host(harness="claude-native") == "ready"
        assert await client.sessions.resolve_online_host(harness="codex-native") is None
        # No harness asked for: any online host will do.
        assert await client.sessions.resolve_online_host() == "needs-auth"
    finally:
        await client.close()
        await transport.aclose()


@pytest.mark.asyncio
async def test_retry_session_posts_the_recovery_event() -> None:
    posted: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            202, json={"queued": False, "recovered": True, "recovery": "runner_relaunched"}
        )

    client, transport = _client(handler)
    try:
        ack = await client.sessions.retry_session("conv_abc123")
    finally:
        await client.close()
        await transport.aclose()

    assert posted == [("/v1/sessions/conv_abc123/events", {"type": "retry_session", "data": {}})]
    assert ack["recovered"] is True
    assert ack["recovery"] == "runner_relaunched"
