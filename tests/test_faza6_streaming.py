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
            config = json.loads(ws_client.receive_text())
            assert config["type"] == "stream.config"
            # We can still communicate
            ws_client.send_text(json.dumps({"type": "ping"}))
            resp = ws_client.receive_text()
            data = json.loads(resp)
            assert data["type"] == "pong"

    def test_stream_config_includes_retention_limit(self):
        from starlette.applications import Starlette
        from starlette.routing import WebSocketRoute
        from starlette.testclient import TestClient

        from amiagi.interfaces.web.app import _ws_events
        from amiagi.interfaces.web.ws.event_hub import EventHub

        app = Starlette(routes=[WebSocketRoute("/ws/events", _ws_events)])
        hub = EventHub()
        app.state.event_hub = hub
        app.state.agent_registry = MagicMock()
        app.state.agent_registry.list_all.return_value = [MagicMock(agent_id="alpha"), MagicMock(agent_id="beta")]

        session = MagicMock()
        session.email = "test@example.com"
        session.session_id = "sess-1"
        sm = AsyncMock()
        sm.validate_session = AsyncMock(return_value=session)
        app.state.session_manager = sm
        client = TestClient(app)

        with client.websocket_connect("/ws/events?token=valid-jwt") as ws_client:
            config = json.loads(ws_client.receive_text())
            assert config["type"] == "stream.config"
            assert config["session_id"] == "sess-1"
            assert config["retention_limit"] == 200
            assert config["active_agents"] == ["kastor", "alpha", "beta"]

    def test_recent_history_is_sent_on_connect(self):
        from starlette.applications import Starlette
        from starlette.routing import WebSocketRoute
        from starlette.testclient import TestClient

        from amiagi.interfaces.web.app import _ws_events
        from amiagi.interfaces.web.ws.event_hub import EventHub

        app = Starlette(routes=[WebSocketRoute("/ws/events", _ws_events)])
        hub = EventHub()
        app.state.event_hub = hub

        session = MagicMock()
        session.email = "test@example.com"
        session.session_id = "sess-1"
        sm = AsyncMock()
        sm.validate_session = AsyncMock(return_value=session)
        app.state.session_manager = sm
        client = TestClient(app)

        asyncio.run(hub.broadcast("log", {"message": "hello", "thread_owners": ["supervisor"]}))

        with client.websocket_connect("/ws/events?token=valid-jwt") as ws_client:
            config = json.loads(ws_client.receive_text())
            assert config["type"] == "stream.config"
            history = json.loads(ws_client.receive_text())
            assert history["type"] == "stream.history"
            assert history["events"][0]["message"] == "hello"

    def test_client_pong_marks_connection_alive(self):
        """A client pong should refresh EventHub last_seen so idle pages are not pruned."""
        from starlette.applications import Starlette
        from starlette.routing import WebSocketRoute
        from starlette.testclient import TestClient

        from amiagi.interfaces.web.app import _ws_events
        from amiagi.interfaces.web.ws.event_hub import EventHub

        app = Starlette(routes=[WebSocketRoute("/ws/events", _ws_events)])
        hub = EventHub()
        app.state.event_hub = hub

        session = MagicMock()
        session.email = "test@example.com"
        sm = AsyncMock()
        sm.validate_session = AsyncMock(return_value=session)
        app.state.session_manager = sm
        client = TestClient(app)

        with client.websocket_connect("/ws/events?token=valid-jwt") as ws_client:
            assert hub.connection_count == 1
            websocket = hub._connections[0]
            before = hub._last_seen[id(websocket)]
            time.sleep(0.01)
            ws_client.send_text(json.dumps({"type": "pong"}))
            deadline = time.time() + 1.0
            while hub._last_seen[id(websocket)] <= before:
                if time.time() >= deadline:
                    raise AssertionError("last_seen was not refreshed by client pong")
                time.sleep(0.01)


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
        assert payload["event_id"] == 1
        assert payload["message_type"] == "log"
        assert payload["from"] == "System"
        assert payload["timestamp"]

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
        assert payload["message_type"] == "actor_state"
        assert payload["status"] == "working"

    @pytest.mark.asyncio
    async def test_get_events_after_filters_since_id(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub

        hub = EventHub()
        await hub.broadcast("log", {"message": "first"})
        await hub.broadcast("log", {"message": "second"})
        await hub.broadcast("log", {"message": "third"})

        events, truncated = hub.get_events_after(2, limit=200)

        assert truncated is False
        assert [event["message"] for event in events] == ["third"]

    @pytest.mark.asyncio
    async def test_get_events_after_marks_truncated_when_gap_exceeds_retention(self):
        from amiagi.interfaces.web.ws.event_hub import EventHub

        hub = EventHub()
        hub._recent_limit = 2
        await hub.broadcast("log", {"message": "first"})
        await hub.broadcast("log", {"message": "second"})
        await hub.broadcast("log", {"message": "third"})
        await hub.broadcast("log", {"message": "fourth"})

        events, truncated = hub.get_events_after(1, limit=200)

        assert truncated is True
        assert [event["message"] for event in events] == ["third", "fourth"]


class TestWSEventHistoryGapFill:
    def test_since_id_returns_only_missing_events(self):
        from starlette.applications import Starlette
        from starlette.routing import WebSocketRoute
        from starlette.testclient import TestClient

        from amiagi.interfaces.web.app import _ws_events
        from amiagi.interfaces.web.ws.event_hub import EventHub

        app = Starlette(routes=[WebSocketRoute("/ws/events", _ws_events)])
        hub = EventHub()
        app.state.event_hub = hub

        session = MagicMock()
        session.email = "test@example.com"
        sm = AsyncMock()
        sm.validate_session = AsyncMock(return_value=session)
        app.state.session_manager = sm
        client = TestClient(app)

        asyncio.run(hub.broadcast("log", {"message": "first", "thread_owners": ["supervisor"]}))
        asyncio.run(hub.broadcast("log", {"message": "second", "thread_owners": ["supervisor"]}))

        with client.websocket_connect("/ws/events?token=valid-jwt&since_id=1") as ws_client:
            config = json.loads(ws_client.receive_text())
            assert config["type"] == "stream.config"
            history = json.loads(ws_client.receive_text())
            assert history["type"] == "stream.history"
            assert history["since_id"] == 1
            assert history["truncated"] is False
            assert [event["message"] for event in history["events"]] == ["second"]

    def test_since_id_marks_history_truncated_when_gap_exceeds_retention(self):
        from starlette.applications import Starlette
        from starlette.routing import WebSocketRoute
        from starlette.testclient import TestClient

        from amiagi.interfaces.web.app import _ws_events
        from amiagi.interfaces.web.ws.event_hub import EventHub

        app = Starlette(routes=[WebSocketRoute("/ws/events", _ws_events)])
        hub = EventHub()
        hub._recent_limit = 2
        app.state.event_hub = hub

        session = MagicMock()
        session.email = "test@example.com"
        sm = AsyncMock()
        sm.validate_session = AsyncMock(return_value=session)
        app.state.session_manager = sm
        client = TestClient(app)

        asyncio.run(hub.broadcast("log", {"message": "first", "thread_owners": ["supervisor"]}))
        asyncio.run(hub.broadcast("log", {"message": "second", "thread_owners": ["supervisor"]}))
        asyncio.run(hub.broadcast("log", {"message": "third", "thread_owners": ["supervisor"]}))
        asyncio.run(hub.broadcast("log", {"message": "fourth", "thread_owners": ["supervisor"]}))

        with client.websocket_connect("/ws/events?token=valid-jwt&since_id=1") as ws_client:
            config = json.loads(ws_client.receive_text())
            assert config["type"] == "stream.config"
            history = json.loads(ws_client.receive_text())
            assert history["type"] == "stream.history"
            assert history["since_id"] == 1
            assert history["truncated"] is True
            assert [event["message"] for event in history["events"]] == ["third", "fourth"]


class TestStreamContract:
    def test_supervisor_stream_event_catalog_covers_known_event_families(self):
        from amiagi.interfaces.web.stream_contract import supervisor_stream_event_catalog

        catalog = supervisor_stream_event_catalog()
        event_types = {entry["type"] for entry in catalog}

        assert "log" in event_types
        assert "actor_state" in event_types
        assert "supervisor_message" in event_types
        assert "operator.input.accepted" in event_types
        assert "agent.lifecycle" in event_types
        assert "workflow.started" in event_types
        assert "eval.completed" in event_types
        assert "knowledge.reindex.started" in event_types
        assert "stream.config" in event_types
        assert len(event_types) == len(catalog)

    def test_supervisor_stream_event_catalog_classifies_communication_vs_technical(self):
        from amiagi.interfaces.web.stream_contract import supervisor_stream_event_catalog

        catalog = {entry["type"]: entry for entry in supervisor_stream_event_catalog()}

        assert catalog["log"]["category"] == "communication"
        assert catalog["actor_state"]["category"] == "communication"
        assert catalog["supervisor_message"]["category"] == "communication"
        assert catalog["error"]["category"] == "technical"
        assert catalog["cycle_finished"]["category"] == "technical"
        assert catalog["operator.input.accepted"]["category"] == "operator"
        assert catalog["workflow.started"]["category"] == "workflow"

    def test_operator_input_targeted_routes_only_to_target_agent(self):
        from amiagi.interfaces.web.stream_contract import routing_for_operator_input

        payload = routing_for_operator_input("alpha")

        assert payload["thread_owners"] == ["agent:alpha"]
        assert payload["direction_per_owner"]["agent:alpha"] == "incoming"

    def test_kastor_actor_routes_only_to_kastor_screen(self):
        from amiagi.interfaces.web.stream_contract import routing_for_actor

        payload = routing_for_actor("kastor")

        assert payload["thread_owners"] == ["agent:kastor"]
        assert "supervisor" not in payload["thread_owners"]

    def test_supervisor_log_panel_routes_only_to_kastor_screen(self):
        from amiagi.interfaces.web.stream_contract import routing_for_panel

        payload = routing_for_panel("supervisor_log")

        assert payload["thread_owners"] == ["agent:kastor"]
        assert payload["direction_per_owner"]["agent:kastor"] == "internal"

    def test_normalize_stream_payload_assigns_supervisor_and_agent_owner_for_eval_reports(self):
        from amiagi.interfaces.web.stream_contract import normalize_stream_payload

        payload = normalize_stream_payload("eval.completed", {"agent_id": "alpha", "run_id": "r-1"})

        assert payload["thread_owners"] == ["supervisor", "agent:alpha"]
        assert payload["to"] == "Supervisor"

    def test_normalize_stream_payload_assigns_supervisor_owner_for_workflow_events(self):
        from amiagi.interfaces.web.stream_contract import normalize_stream_payload

        payload = normalize_stream_payload("workflow.started", {"run_id": "wf-1"})

        assert payload["thread_owners"] == ["supervisor"]

    def test_router_actor_routes_only_to_router_screen(self):
        from amiagi.interfaces.web.stream_contract import routing_for_actor

        payload = routing_for_actor("router")

        assert payload["thread_owners"] == ["agent:router"]
        assert payload["direction_per_owner"]["agent:router"] == "internal"

    def test_terminal_actor_routes_only_to_router_screen(self):
        from amiagi.interfaces.web.stream_contract import routing_for_actor

        payload = routing_for_actor("terminal")

        assert payload["thread_owners"] == ["agent:router"]

    def test_user_model_log_routes_to_supervisor_and_agent(self):
        from amiagi.interfaces.web.stream_contract import routing_for_panel

        payload = routing_for_panel("user_model_log", agent_id="polluks")

        assert payload["thread_owners"] == ["supervisor", "agent:polluks"]
        assert payload["direction_per_owner"]["supervisor"] == "incoming"

    def test_panel_routing_for_agent_stream_does_not_include_supervisor(self):
        from amiagi.interfaces.web.stream_contract import routing_for_panel

        payload = routing_for_panel("model", agent_id="alpha")

        assert payload["thread_owners"] == ["agent:alpha"]
        assert "supervisor" not in payload["thread_owners"]
        assert payload["direction_per_owner"]["agent:alpha"] == "internal"

    def test_supervisor_message_routes_to_supervisor_and_kastor(self):
        from amiagi.interfaces.web.stream_contract import routing_for_supervisor_message

        payload = routing_for_supervisor_message()

        assert payload["thread_owners"] == ["supervisor", "agent:kastor"]
        assert payload["direction_per_owner"]["supervisor"] == "incoming"

    def test_normalize_stream_payload_routes_agent_inbox_request_to_supervisor_and_agent(self):
        from amiagi.interfaces.web.stream_contract import normalize_stream_payload

        payload = normalize_stream_payload("inbox.new", {"agent_id": "nova", "item_type": "review_request"})

        assert payload["thread_owners"] == ["supervisor", "agent:nova"]

    def test_normalize_stream_payload_routes_task_reassign_only_to_target_agent(self):
        from amiagi.interfaces.web.stream_contract import normalize_stream_payload

        payload = normalize_stream_payload("task.reassigned", {"assigned_agent_id": "nova", "task_id": "t-1"})

        assert payload["thread_owners"] == ["agent:nova"]
        assert "supervisor" not in payload["thread_owners"]

    def test_normalize_stream_payload_routes_task_completion_to_supervisor_and_agent(self):
        from amiagi.interfaces.web.stream_contract import normalize_stream_payload

        payload = normalize_stream_payload(
            "task.moved",
            {"assigned_agent_id": "nova", "task_id": "t-2", "to_status": "done"},
        )

        assert payload["thread_owners"] == ["supervisor", "agent:nova"]
        assert payload["direction_per_owner"]["agent:nova"] == "internal"


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
