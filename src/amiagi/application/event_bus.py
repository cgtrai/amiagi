"""EventBus — lightweight typed pub/sub for RouterEngine → adapter communication.

Events are plain dataclasses.  Subscribers register with ``on(EventType, callback)``
and the bus dispatches synchronously on the emitting thread.  The adapter is
responsible for marshalling to the correct thread (e.g. ``call_from_thread``
in Textual).
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

__all__ = [
    "EventBus",
    "LogEvent",
    "ActorStateEvent",
    "CycleFinishedEvent",
    "SupervisorMessageEvent",
    "ErrorEvent",
]

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LogEvent:
    """A log line that should be displayed in a named panel."""

    panel: str  # "user_model_log", "executor_log", "supervisor_log"
    message: str
    agent_id: str | None = None
    channel: str | None = None
    source_kind: str | None = None
    source_label: str | None = None
    summary: str | None = None


@dataclass(slots=True)
class ActorStateEvent:
    """An actor's state has changed."""

    actor: str  # "router", "creator", "supervisor", "terminal"
    state: str  # "ACTIVE", "THINKING", "PAUSED", …
    event: str  # human-readable description
    agent_id: str | None = None
    channel: str | None = None
    source_kind: str | None = None
    source_label: str | None = None
    summary: str | None = None


@dataclass(slots=True)
class CycleFinishedEvent:
    """A router cycle has completed."""

    event: str


@dataclass(slots=True)
class SupervisorMessageEvent:
    """Kastor produced a supervision message for the outbox."""

    stage: str
    reason_code: str
    notes: str
    answer: str


@dataclass(slots=True)
class ErrorEvent:
    """A non-fatal error that the adapter should surface to the user."""

    message: str


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

_E = TypeVar("_E")


class EventBus:
    """Thread-safe typed event bus.

    Usage::

        bus = EventBus()
        bus.on(LogEvent, lambda e: print(e.message))
        bus.emit(LogEvent(panel="user_model_log", message="hello"))
    """

    def __init__(self) -> None:
        self._subscribers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)
        self._lock = threading.Lock()

    # -- subscription -------------------------------------------------------

    def on(self, event_type: type[_E], callback: Callable[[_E], None]) -> None:
        """Register *callback* for events of *event_type*."""
        with self._lock:
            self._subscribers[event_type].append(callback)

    def off(self, event_type: type[_E], callback: Callable[[_E], None]) -> None:
        """Remove a previously registered callback (no-op if not found)."""
        with self._lock:
            try:
                self._subscribers[event_type].remove(callback)
            except ValueError:
                pass

    # -- emission -----------------------------------------------------------

    def emit(self, event: Any) -> None:
        """Dispatch *event* to all registered subscribers (synchronously).

        Callbacks are invoked **on the calling thread**.  If a callback raises,
        the exception is suppressed and remaining callbacks still execute.
        """
        with self._lock:
            handlers = list(self._subscribers.get(type(event), []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # Adapter is responsible for error handling; we never let
                # a broken subscriber kill the engine.
                pass

    # -- introspection ------------------------------------------------------

    def subscriber_count(self, event_type: type | None = None) -> int:
        """Return the number of subscribers, optionally filtered by type."""
        with self._lock:
            if event_type is not None:
                return len(self._subscribers.get(event_type, []))
            return sum(len(v) for v in self._subscribers.values())

    def clear(self) -> None:
        """Remove all subscribers."""
        with self._lock:
            self._subscribers.clear()
