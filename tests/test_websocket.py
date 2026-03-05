"""Tests for EventHub WebSocket handler — connect, disconnect, broadcast, heartbeat."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amiagi.interfaces.web.ws.event_hub import EventHub


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_ws(client: str = "127.0.0.1:9999") -> AsyncMock:
    ws = AsyncMock()
    ws.client = client
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


# ------------------------------------------------------------------
# Connection lifecycle
# ------------------------------------------------------------------

class TestConnectDisconnect:

    @pytest.mark.asyncio
    async def test_connect_accepts_and_registers(self) -> None:
        hub = EventHub()
        ws = _make_ws()
        await hub.connect(ws)
        assert hub.connection_count == 1
        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_multiple(self) -> None:
        hub = EventHub()
        ws1, ws2 = _make_ws("a"), _make_ws("b")
        await hub.connect(ws1)
        await hub.connect(ws2)
        assert hub.connection_count == 2

    @pytest.mark.asyncio
    async def test_disconnect_removes(self) -> None:
        hub = EventHub()
        ws = _make_ws()
        await hub.connect(ws)
        await hub.disconnect(ws)
        assert hub.connection_count == 0

    @pytest.mark.asyncio
    async def test_disconnect_non_existing_is_safe(self) -> None:
        hub = EventHub()
        ws = _make_ws()
        await hub.disconnect(ws)  # no error
        assert hub.connection_count == 0


# ------------------------------------------------------------------
# Broadcast
# ------------------------------------------------------------------

class TestBroadcast:

    @pytest.mark.asyncio
    async def test_broadcast_to_single_client(self) -> None:
        hub = EventHub()
        ws = _make_ws()
        await hub.connect(ws)
        await hub.broadcast("agent.update", {"agent_id": "a1", "state": "busy"})
        ws.send_text.assert_awaited_once()
        msg = json.loads(ws.send_text.call_args[0][0])
        assert msg["type"] == "agent.update"
        assert msg["agent_id"] == "a1"

    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_clients(self) -> None:
        hub = EventHub()
        ws1, ws2 = _make_ws("a"), _make_ws("b")
        await hub.connect(ws1)
        await hub.connect(ws2)
        await hub.broadcast("ping", {})
        assert ws1.send_text.await_count == 1
        assert ws2.send_text.await_count == 1

    @pytest.mark.asyncio
    async def test_broadcast_empty_connections(self) -> None:
        hub = EventHub()
        await hub.broadcast("event", {"data": 1})  # no error

    @pytest.mark.asyncio
    async def test_broadcast_removes_stale_client(self) -> None:
        hub = EventHub()
        ws = _make_ws()
        await hub.connect(ws)
        ws.send_text.side_effect = RuntimeError("closed")
        await hub.broadcast("test", {})
        assert hub.connection_count == 0


# ------------------------------------------------------------------
# Per-agent listeners
# ------------------------------------------------------------------

class TestAgentListeners:

    @pytest.mark.asyncio
    async def test_register_agent_listener(self) -> None:
        hub = EventHub()
        ws = _make_ws()
        await hub.connect(ws)
        hub.register_agent_listener("agent-1", ws)
        assert hub.agent_listener_count == 1

    @pytest.mark.asyncio
    async def test_unregister_agent_listener(self) -> None:
        hub = EventHub()
        ws = _make_ws()
        hub.register_agent_listener("agent-1", ws)
        hub.unregister_agent_listener("agent-1", ws)
        assert hub.agent_listener_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_reaches_agent_listener(self) -> None:
        hub = EventHub()
        global_ws = _make_ws("global")
        agent_ws = _make_ws("agent")
        await hub.connect(global_ws)
        hub.register_agent_listener("a1", agent_ws)

        await hub.broadcast("update", {"agent_id": "a1", "val": 42})
        # Both should receive
        global_ws.send_text.assert_awaited_once()
        agent_ws.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_broadcast_agent_listener_other_agent_no_message(self) -> None:
        hub = EventHub()
        ws = _make_ws()
        hub.register_agent_listener("a1", ws)
        await hub.broadcast("update", {"agent_id": "a2", "val": 1})
        ws.send_text.assert_not_awaited()


# ------------------------------------------------------------------
# Heartbeat & mark_alive
# ------------------------------------------------------------------

class TestHeartbeat:

    def test_mark_alive(self) -> None:
        hub = EventHub()
        ws = _make_ws()
        hub.mark_alive(ws)
        assert id(ws) in hub._last_seen

    @pytest.mark.asyncio
    async def test_start_stop_heartbeat(self) -> None:
        hub = EventHub()
        hub.start_heartbeat()
        assert hub._heartbeat_task is not None
        hub.stop_heartbeat()
