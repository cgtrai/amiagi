"""Tests for EventBus — typed pub/sub infrastructure."""

from __future__ import annotations

import threading

from amiagi.application.event_bus import (
    ActorStateEvent,
    CycleFinishedEvent,
    ErrorEvent,
    EventBus,
    LogEvent,
    SupervisorMessageEvent,
)


class TestEventBusSubscription:
    def test_on_and_emit(self) -> None:
        bus = EventBus()
        received: list[LogEvent] = []
        bus.on(LogEvent, received.append)
        bus.emit(LogEvent(panel="user_model_log", message="hello"))
        assert len(received) == 1
        assert received[0].message == "hello"
        assert received[0].panel == "user_model_log"

    def test_multiple_subscribers(self) -> None:
        bus = EventBus()
        a: list[LogEvent] = []
        b: list[LogEvent] = []
        bus.on(LogEvent, a.append)
        bus.on(LogEvent, b.append)
        bus.emit(LogEvent(panel="p", message="m"))
        assert len(a) == 1
        assert len(b) == 1

    def test_different_event_types_isolated(self) -> None:
        bus = EventBus()
        logs: list[LogEvent] = []
        states: list[ActorStateEvent] = []
        bus.on(LogEvent, logs.append)
        bus.on(ActorStateEvent, states.append)
        bus.emit(LogEvent(panel="p", message="m"))
        bus.emit(ActorStateEvent(actor="router", state="ACTIVE", event="test"))
        assert len(logs) == 1
        assert len(states) == 1

    def test_off_removes_subscriber(self) -> None:
        bus = EventBus()
        received: list[LogEvent] = []
        bus.on(LogEvent, received.append)
        bus.off(LogEvent, received.append)
        bus.emit(LogEvent(panel="p", message="m"))
        assert len(received) == 0

    def test_off_nonexistent_is_noop(self) -> None:
        bus = EventBus()
        bus.off(LogEvent, lambda e: None)  # no error

    def test_emit_no_subscribers_is_noop(self) -> None:
        bus = EventBus()
        bus.emit(LogEvent(panel="p", message="m"))  # no error

    def test_subscriber_exception_suppressed(self) -> None:
        bus = EventBus()
        ok: list[LogEvent] = []

        def fail(_e: LogEvent) -> None:
            raise RuntimeError("boom")

        bus.on(LogEvent, fail)
        bus.on(LogEvent, ok.append)
        bus.emit(LogEvent(panel="p", message="m"))
        assert len(ok) == 1  # second subscriber still runs

    def test_clear(self) -> None:
        bus = EventBus()
        received: list[LogEvent] = []
        bus.on(LogEvent, received.append)
        bus.clear()
        bus.emit(LogEvent(panel="p", message="m"))
        assert len(received) == 0

    def test_subscriber_count(self) -> None:
        bus = EventBus()
        assert bus.subscriber_count() == 0
        bus.on(LogEvent, lambda e: None)
        bus.on(ActorStateEvent, lambda e: None)
        assert bus.subscriber_count() == 2
        assert bus.subscriber_count(LogEvent) == 1
        assert bus.subscriber_count(ActorStateEvent) == 1
        assert bus.subscriber_count(ErrorEvent) == 0


class TestEventBusThreadSafety:
    def test_concurrent_emit(self) -> None:
        bus = EventBus()
        received: list[LogEvent] = []
        lock = threading.Lock()

        def safe_append(e: LogEvent) -> None:
            with lock:
                received.append(e)

        bus.on(LogEvent, safe_append)

        threads = [
            threading.Thread(
                target=lambda i=i: bus.emit(LogEvent(panel="p", message=f"m{i}"))
            )
            for i in range(100)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(received) == 100


class TestEventDataclasses:
    def test_log_event_slots(self) -> None:
        e = LogEvent(panel="p", message="m")
        assert e.panel == "p"
        assert e.message == "m"

    def test_actor_state_event(self) -> None:
        e = ActorStateEvent(actor="router", state="ACTIVE", event="test")
        assert e.actor == "router"

    def test_cycle_finished_event(self) -> None:
        e = CycleFinishedEvent(event="done")
        assert e.event == "done"

    def test_supervisor_message_event(self) -> None:
        e = SupervisorMessageEvent(
            stage="s", reason_code="OK", notes="n", answer="a"
        )
        assert e.stage == "s"

    def test_error_event(self) -> None:
        e = ErrorEvent(message="err")
        assert e.message == "err"
