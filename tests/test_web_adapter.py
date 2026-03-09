"""Tests for WebAdapter — EventBus subscription and broadcast scheduling."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from amiagi.application.event_bus import (
    ActorStateEvent,
    CycleFinishedEvent,
    ErrorEvent,
    EventBus,
    LogEvent,
    SupervisorMessageEvent,
)
from amiagi.interfaces.web.web_adapter import WebAdapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _FakeRouterEngine:
    """Minimal RouterEngine mock."""

    def __init__(self):
        self.submitted: list[str] = []

    def submit_user_turn(self, text: str) -> None:
        self.submitted.append(text)


@pytest.fixture()
def event_bus():
    return EventBus()


@pytest.fixture()
def router_engine():
    return _FakeRouterEngine()


@pytest.fixture()
def adapter(event_bus, router_engine):
    return WebAdapter(event_bus=event_bus, router_engine=router_engine)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWebAdapterInit:
    """WebAdapter initialisation and attribute access."""

    def test_init_stores_event_bus(self, adapter, event_bus):
        assert adapter._event_bus is event_bus

    def test_init_stores_router_engine(self, adapter, router_engine):
        assert adapter._router_engine is router_engine

    def test_router_engine_property(self, adapter, router_engine):
        assert adapter.router_engine is router_engine


class TestWebAdapterStart:
    """EventBus subscription on start()."""

    def test_start_subscribes_all_event_types(self, adapter, event_bus):
        adapter.start()
        assert event_bus.subscriber_count(LogEvent) >= 1
        assert event_bus.subscriber_count(ActorStateEvent) >= 1
        assert event_bus.subscriber_count(CycleFinishedEvent) >= 1
        assert event_bus.subscriber_count(SupervisorMessageEvent) >= 1
        assert event_bus.subscriber_count(ErrorEvent) >= 1

    def test_start_subscribes_exactly_5(self, adapter, event_bus):
        adapter.start()
        assert event_bus.subscriber_count() == 5

    def test_stop_unsubscribes(self, adapter, event_bus):
        adapter.start()
        adapter.stop()
        assert event_bus.subscriber_count() == 0


class TestWebAdapterBroadcast:
    """EventBus emit → scheduled broadcast."""

    def test_log_event_schedules_broadcast(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        event_bus.emit(LogEvent(panel="executor_log", message="hello"))

        # run_coroutine_threadsafe was called; run pending
        loop.run_until_complete(asyncio.sleep(0.05))
        hub.broadcast.assert_called_once()
        call_args = hub.broadcast.call_args
        assert call_args[0][0] == "log"
        assert call_args[0][1]["panel"] == "executor_log"
        assert call_args[0][1]["message"] == "hello"
        assert call_args[0][1]["channel"] == "executor"
        assert call_args[0][1]["source_label"] == "Polluks"
        assert call_args[0][1]["summary"] == "Polluks: hello"
        loop.close()

    def test_actor_state_event(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        event_bus.emit(ActorStateEvent(actor="kastor", state="ACTIVE", event="started"))
        loop.run_until_complete(asyncio.sleep(0.05))
        hub.broadcast.assert_called_once()
        assert hub.broadcast.call_args[0][0] == "actor_state"
        payload = hub.broadcast.call_args[0][1]
        assert payload["channel"] == "supervisor"
        assert payload["source_label"] == "Kastor"
        assert payload["summary"] == "Kastor · ACTIVE · started"
        loop.close()

    def test_log_event_enriches_agent_id(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        event_bus.emit(LogEvent(panel="executor_log", message="hello"))

        loop.run_until_complete(asyncio.sleep(0.05))
        payload = hub.broadcast.call_args[0][1]
        assert payload["agent_id"] == "polluks"
        loop.close()

    def test_log_event_prefers_explicit_metadata(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        event_bus.emit(LogEvent(
            panel="executor_log",
            message="hello",
            agent_id="agent-custom",
            channel="supervisor",
            source_kind="agent",
            source_label="Custom",
            summary="Custom summary",
        ))

        loop.run_until_complete(asyncio.sleep(0.05))
        payload = hub.broadcast.call_args[0][1]
        assert payload["agent_id"] == "agent-custom"
        assert payload["channel"] == "supervisor"
        assert payload["source_label"] == "Custom"
        assert payload["summary"] == "Custom summary"
        loop.close()

    def test_supervisor_event_targets_kastor(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        event_bus.emit(SupervisorMessageEvent(
            stage="review",
            reason_code="OK",
            notes="note",
            answer="answer",
        ))

        loop.run_until_complete(asyncio.sleep(0.05))
        payload = hub.broadcast.call_args[0][1]
        assert payload["agent_id"] == "kastor"
        assert payload["channel"] == "supervisor"
        assert payload["summary"] == "note"
        loop.close()

    def test_actor_state_prefers_explicit_metadata(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        event_bus.emit(ActorStateEvent(
            actor="kastor",
            state="ACTIVE",
            event="started",
            agent_id="agent-77",
            channel="executor",
            source_kind="agent",
            source_label="Explicit",
            summary="Explicit actor summary",
        ))

        loop.run_until_complete(asyncio.sleep(0.05))
        payload = hub.broadcast.call_args[0][1]
        assert payload["agent_id"] == "agent-77"
        assert payload["channel"] == "executor"
        assert payload["source_label"] == "Explicit"
        assert payload["summary"] == "Explicit actor summary"
        loop.close()

    def test_error_event(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        event_bus.emit(ErrorEvent(message="something broke"))
        loop.run_until_complete(asyncio.sleep(0.05))
        hub.broadcast.assert_called_once()
        assert hub.broadcast.call_args[0][0] == "error"
        payload = hub.broadcast.call_args[0][1]
        assert payload["channel"] == "system"
        assert payload["summary"] == "something broke"
        loop.close()

    def test_cycle_event_carries_system_summary(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        event_bus.emit(CycleFinishedEvent(event="completed"))

        loop.run_until_complete(asyncio.sleep(0.05))
        payload = hub.broadcast.call_args[0][1]
        assert hub.broadcast.call_args[0][0] == "cycle_finished"
        assert payload["channel"] == "system"
        assert payload["summary"] == "Cycle finished · completed"
        loop.close()

    def test_no_broadcast_without_hub(self, adapter, event_bus):
        """No crash when hub is not attached."""
        adapter.start()
        # No hub / loop set — should silently skip
        event_bus.emit(LogEvent(panel="test", message="ignored"))


class TestWebAdapterSubmit:
    """submit_user_turn delegates to RouterEngine."""

    def test_submit_user_turn(self, adapter, router_engine):
        adapter.submit_user_turn("hello agent")
        assert router_engine.submitted == ["hello agent"]

    def test_submit_user_turn_multiple(self, adapter, router_engine):
        adapter.submit_user_turn("first")
        adapter.submit_user_turn("second")
        assert len(router_engine.submitted) == 2
