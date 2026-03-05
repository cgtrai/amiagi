"""WebAdapter — bridges EventBus events to WebSocket clients.

Subscribes to all EventBus event types and broadcasts serialised JSON
to connected WebSocket clients via the EventHub.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from amiagi.application.event_bus import EventBus
    from amiagi.application.router_engine import RouterEngine
    from amiagi.interfaces.web.ws.event_hub import EventHub

logger = logging.getLogger(__name__)


class WebAdapter:
    """Subscribe to EventBus → push events to WebSocket clients via EventHub."""

    def __init__(
        self,
        event_bus: "EventBus",
        router_engine: "RouterEngine",
    ) -> None:
        self._event_bus = event_bus
        self._router_engine = router_engine
        self._event_hub: "EventHub | None" = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_event_hub(self, hub: "EventHub") -> None:
        """Attach the WebSocket hub (called during app startup)."""
        self._event_hub = hub

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the running asyncio event loop for thread-safe scheduling."""
        self._loop = loop

    def start(self) -> None:
        """Subscribe to all EventBus event types."""
        from amiagi.application.event_bus import (
            ActorStateEvent,
            CycleFinishedEvent,
            ErrorEvent,
            LogEvent,
            SupervisorMessageEvent,
        )

        self._event_bus.on(LogEvent, self._on_log)
        self._event_bus.on(ActorStateEvent, self._on_actor_state)
        self._event_bus.on(CycleFinishedEvent, self._on_cycle)
        self._event_bus.on(SupervisorMessageEvent, self._on_supervisor)
        self._event_bus.on(ErrorEvent, self._on_error)
        logger.info("WebAdapter subscribed to EventBus (%d handlers)", 5)

    def stop(self) -> None:
        """Unsubscribe from EventBus."""
        from amiagi.application.event_bus import (
            ActorStateEvent,
            CycleFinishedEvent,
            ErrorEvent,
            LogEvent,
            SupervisorMessageEvent,
        )

        self._event_bus.off(LogEvent, self._on_log)
        self._event_bus.off(ActorStateEvent, self._on_actor_state)
        self._event_bus.off(CycleFinishedEvent, self._on_cycle)
        self._event_bus.off(SupervisorMessageEvent, self._on_supervisor)
        self._event_bus.off(ErrorEvent, self._on_error)
        logger.info("WebAdapter unsubscribed from EventBus.")

    def submit_user_turn(self, text: str) -> None:
        """Forward a user prompt to the RouterEngine."""
        self._router_engine.submit_user_turn(text)

    @property
    def router_engine(self) -> "RouterEngine":
        return self._router_engine

    # ------------------------------------------------------------------
    # EventBus callbacks (called from RouterEngine thread)
    # ------------------------------------------------------------------

    def _on_log(self, event: Any) -> None:
        self._schedule_broadcast("log", {
            "panel": event.panel,
            "message": event.message,
        })

    def _on_actor_state(self, event: Any) -> None:
        self._schedule_broadcast("actor_state", {
            "actor": event.actor,
            "state": event.state,
            "event": event.event,
        })

    def _on_cycle(self, event: Any) -> None:
        self._schedule_broadcast("cycle_finished", {
            "event": event.event,
        })

    def _on_supervisor(self, event: Any) -> None:
        self._schedule_broadcast("supervisor_message", {
            "stage": event.stage,
            "reason_code": event.reason_code,
            "notes": event.notes,
            "answer": event.answer,
        })

    def _on_error(self, event: Any) -> None:
        self._schedule_broadcast("error", {
            "message": event.message,
        })

    # ------------------------------------------------------------------
    # Thread-safe broadcast helper
    # ------------------------------------------------------------------

    def _schedule_broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        """Schedule an async broadcast from the synchronous EventBus thread."""
        if self._event_hub is None or self._loop is None:
            return
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        hub = self._event_hub
        asyncio.run_coroutine_threadsafe(
            hub.broadcast(event_type, payload),
            self._loop,
        )
