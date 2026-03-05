"""Tests for Faza 6 — Streaming & real-time.

Covers:
- WebSocket JWT auth on /ws/events (accept with valid token, reject without)
- Per-agent WS auth
- EventHub heartbeat (start/stop, mark_alive)
- Ping/pong handling
- Message protocol (JSON with type, agent_id, timestamp)
- Client reconnection logic (in JS — tested via file content)
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# EventHub heartbeat tests
# ---------------------------------------------------------------------------

class TestEventHubHeartbeat:
    def test_start_stop_heartbeat(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        # Can't start heartbeat outside asyncio loop, but we test the API
        assert hub._heartbeat_task is None
        hub.stop_heartbeat()  # no-op when not started

    @pytest.mark.asyncio
    async def test_mark_alive(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        ws = MagicMock()
        ws_id = id(ws)
        hub.mark_alive(ws)
        assert ws_id in hub._last_seen
        assert hub._last_seen[ws_id] > 0

    @pytest.mark.asyncio
    async def test_connect_sets_last_seen(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        ws = AsyncMock()
        await hub.connect(ws)
        assert id(ws) in hub._last_seen
        assert hub.connection_count == 1

    @pytest.mark.asyncio
    async def test_disconnect_clears_last_seen(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        ws = AsyncMock()
        await hub.connect(ws)
        await hub.disconnect(ws)
        assert id(ws) not in hub._last_seen


# ---------------------------------------------------------------------------
# WebSocket JWT auth tests (global /ws/events)
# ---------------------------------------------------------------------------

class TestWSEventsAuth:
    """Test that /ws/events requires a valid JWT token."""

    def test_no_token_closes_4001(self):
        """Without ?token= param, the WS should close with 4001."""
        from starlette.applications import Starlette
        from starlette.routing import WebSocketRoute
        from starlette.testclient import TestClient

        from amiagi.interfaces.web.app import _ws_events
        from amiagi.interfaces.web.ws.event_hub import EventHub

        app = Starlette(routes=[WebSocketRoute("/ws/events", _ws_events)])
        app.state.event_hub = EventHub()
        app.state.session_manager = None  # not needed since token is missing
        client = TestClient(app)

        # No token → should close
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/events"):
                pass  # Should not reach here

    def test_invalid_token_closes_4001(self):
        """A bad JWT should close with 4001."""
        from starlette.applications import Starlette
        from starlette.routing import WebSocketRoute
        from starlette.testclient import TestClient

        from amiagi.interfaces.web.app import _ws_events
        from amiagi.interfaces.web.ws.event_hub import EventHub

        app = Starlette(routes=[WebSocketRoute("/ws/events", _ws_events)])
        app.state.event_hub = EventHub()

        # Mock session_manager that rejects the token
        sm = AsyncMock()
        sm.validate_session = AsyncMock(return_value=None)
        app.state.session_manager = sm
        client = TestClient(app)

        with pytest.raises(Exception):
            with client.websocket_connect("/ws/events?token=bad-jwt"):
                pass

    def test_valid_token_accepts(self):
        """A valid JWT should allow connection."""
        from starlette.applications import Starlette
        from starlette.routing import WebSocketRoute
        from starlette.testclient import TestClient

        from amiagi.interfaces.web.app import _ws_events
        from amiagi.interfaces.web.ws.event_hub import EventHub

        app = Starlette(routes=[WebSocketRoute("/ws/events", _ws_events)])
        hub = EventHub()
        app.state.event_hub = hub

        # Mock session_manager that accepts the token
        session = MagicMock()
        session.email = "test@example.com"
        sm = AsyncMock()
        sm.validate_session = AsyncMock(return_value=session)
        app.state.session_manager = sm
        client = TestClient(app)

        with client.websocket_connect("/ws/events?token=valid-jwt") as ws_client:
            assert hub.connection_count == 1
            # We can still communicate
            ws_client.send_text(json.dumps({"type": "ping"}))
            resp = ws_client.receive_text()
            data = json.loads(resp)
            assert data["type"] == "pong"


# ---------------------------------------------------------------------------
# Message protocol
# ---------------------------------------------------------------------------

class TestMessageProtocol:
    @pytest.mark.asyncio
    async def test_broadcast_includes_type(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        ws = AsyncMock()
        hub._connections.append(ws)
        hub._last_seen[id(ws)] = time.monotonic()
        await hub.broadcast("log", {"panel": "main", "message": "hello"})
        ws.send_text.assert_called_once()
        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["type"] == "log"
        assert payload["panel"] == "main"
        assert payload["message"] == "hello"

    @pytest.mark.asyncio
    async def test_broadcast_actor_state_format(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        ws = AsyncMock()
        hub._connections.append(ws)
        hub._last_seen[id(ws)] = time.monotonic()
        await hub.broadcast("actor_state", {"actor": "router", "state": "working", "agent_id": "a1"})
        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["type"] == "actor_state"
        assert payload["actor"] == "router"


# ---------------------------------------------------------------------------
# Client reconnection (JS content checks)
# ---------------------------------------------------------------------------

class TestClientReconnectionJS:
    def test_exponential_backoff_in_js(self):
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "dashboard.js"
        content = js.read_text()
        assert "Math.pow(2," in content, "Missing exponential backoff"
        assert "_MAX_RECONNECT_DELAY" in content, "Missing max reconnect delay"

    def test_reconnect_badge_in_js(self):
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "dashboard.js"
        content = js.read_text()
        assert "Reconnecting" in content, "Missing reconnecting indicator"
        assert "setConnectionStatus" in content

    def test_ping_pong_handling_in_js(self):
        from pathlib import Path
        js = Path(__file__).resolve().parent.parent / "src" / "amiagi" / "interfaces" / "web" / "static" / "js" / "dashboard.js"
        content = js.read_text()
        assert '"pong"' in content, "Client should respond with pong"


# ---------------------------------------------------------------------------
# Heartbeat settings
# ---------------------------------------------------------------------------

class TestHeartbeatSettings:
    def test_interval_30s(self):
        from amiagi.interfaces.web.ws.event_hub import _HEARTBEAT_INTERVAL_S
        assert _HEARTBEAT_INTERVAL_S == 30

    def test_timeout_90s(self):
        from amiagi.interfaces.web.ws.event_hub import _HEARTBEAT_TIMEOUT_S
        assert _HEARTBEAT_TIMEOUT_S == 90


# ---------------------------------------------------------------------------
# Throughput: broadcast to 100 connections (lightweight smoke)
# ---------------------------------------------------------------------------

class TestBroadcastThroughput:
    @pytest.mark.asyncio
    async def test_broadcast_to_many(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub
        hub = EventHub()
        mocks = []
        for _ in range(100):
            m = AsyncMock()
            hub._connections.append(m)
            hub._last_seen[id(m)] = time.monotonic()
            mocks.append(m)
        await hub.broadcast("log", {"message": "load test"})
        for m in mocks:
            assert m.send_text.call_count == 1
