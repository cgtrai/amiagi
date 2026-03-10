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
        assert call_args[0][1]["from"] == "Polluks"
        assert call_args[0][1]["to"] == "polluks"
        assert call_args[0][1]["message_type"] == "log"
        assert call_args[0][1]["timestamp"]
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
        assert payload["thread_owners"] == ["agent:kastor"]
        assert payload["from"] == "Kastor"
        assert payload["to"] == "kastor"
        assert payload["message_type"] == "actor_state"
        assert payload["status"] == "active"
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

    def test_user_model_log_routes_to_supervisor_and_polluks(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        event_bus.emit(LogEvent(panel="user_model_log", message="Model: gotowe", agent_id="polluks", source_label="Polluks"))

        loop.run_until_complete(asyncio.sleep(0.05))
        payload = hub.broadcast.call_args[0][1]
        assert payload["thread_owners"] == ["supervisor", "agent:polluks"]
        assert payload["from"] == "Polluks"
        assert payload["to"] == "Supervisor"
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
            answer="[Polluks -> Sponsor] answer",
        ))

        loop.run_until_complete(asyncio.sleep(0.05))
        assert hub.broadcast.await_count == 2
        internal_payload = hub.broadcast.await_args_list[0].args[1]
        report_payload = hub.broadcast.await_args_list[1].args[1]

        assert internal_payload["agent_id"] == "kastor"
        assert internal_payload["channel"] == "supervisor"
        assert internal_payload["summary"] == "note"
        assert internal_payload["thread_owners"] == ["agent:polluks"]
        assert internal_payload["from"] == "Kastor"
        assert internal_payload["to"] == "Polluks"
        assert internal_payload["message_type"] == "supervisor_message"

        assert report_payload["agent_id"] == "kastor"
        assert report_payload["channel"] == "supervisor"
        assert report_payload["summary"] == "answer"
        assert report_payload["thread_owners"] == ["supervisor", "agent:kastor"]
        assert report_payload["from"] == "Kastor"
        assert report_payload["to"] == "Sponsor"
        assert report_payload["message_type"] == "supervisor_message"
        loop.close()

    def test_internal_supervisor_tool_message_targets_polluks_only(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        event_bus.emit(SupervisorMessageEvent(
            stage="tool_flow",
            reason_code="OK",
            notes="Polluks, pamiętaj o protokole",
            answer='```tool_call\n{"tool":"fetch_web","args":{"url":"https://example.com"},"intent":"scan"}\n```',
        ))

        loop.run_until_complete(asyncio.sleep(0.05))
        assert hub.broadcast.await_count == 1
        payload = hub.broadcast.await_args_list[0].args[1]
        assert payload["thread_owners"] == ["agent:polluks"]
        assert payload["to"] == "Polluks"
        assert payload["summary"] == "Polluks, pamiętaj o protokole"
        loop.close()

    def test_duplicate_sponsor_supervisor_summary_is_not_broadcast_twice(self, adapter, event_bus):
        adapter.start()
        hub = MagicMock()
        hub.broadcast = AsyncMock()
        loop = asyncio.new_event_loop()
        adapter.set_event_hub(hub)
        adapter.set_loop(loop)

        duplicate_event = SupervisorMessageEvent(
            stage="review",
            reason_code="OK",
            notes="note one",
            answer="[Polluks -> Sponsor] status bez zmian",
        )

        event_bus.emit(duplicate_event)
        loop.run_until_complete(asyncio.sleep(0.05))
        event_bus.emit(SupervisorMessageEvent(
            stage="review",
            reason_code="OK",
            notes="note two",
            answer="[Polluks -> Sponsor] status bez zmian",
        ))
        loop.run_until_complete(asyncio.sleep(0.05))

        sponsor_payloads = [
            call.args[1]
            for call in hub.broadcast.await_args_list
            if call.args[1].get("to") == "Sponsor"
        ]
        assert len(sponsor_payloads) == 1
        assert sponsor_payloads[0]["summary"] == "status bez zmian"
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
        assert payload["message_type"] == "error"
        assert payload["status"] == "error"
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
        assert payload["thread_owners"] == ["agent:router"]
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
