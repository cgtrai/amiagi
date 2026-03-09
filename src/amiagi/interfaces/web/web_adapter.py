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

from amiagi.interfaces.web.stream_contract import (
    infer_agent_id,
    stream_meta_for_actor,
    stream_meta_for_panel,
    summarize_actor_state,
    summarize_log_event,
)

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
        stream_meta = self._stream_meta_for_panel(event.panel)
        payload = {
            "panel": event.panel,
            "message": event.message,
            "channel": event.channel or stream_meta.get("channel"),
            "source_kind": event.source_kind or stream_meta.get("source_kind"),
            "source_label": event.source_label or stream_meta.get("source_label"),
            "summary": event.summary or summarize_log_event(event.panel, event.message, event.source_label or stream_meta.get("source_label")),
        }
        agent_id = event.agent_id or stream_meta.get("agent_id")
        if agent_id is not None:
            payload["agent_id"] = agent_id
        self._schedule_broadcast("log", payload)

    def _on_actor_state(self, event: Any) -> None:
        stream_meta = self._stream_meta_for_actor(event.actor)
        payload = {
            "actor": event.actor,
            "state": event.state,
            "event": event.event,
            "channel": event.channel or stream_meta.get("channel"),
            "source_kind": event.source_kind or stream_meta.get("source_kind"),
            "source_label": event.source_label or stream_meta.get("source_label"),
            "summary": event.summary or summarize_actor_state(event.actor, event.state, event.event, event.source_label or stream_meta.get("source_label")),
        }
        agent_id = event.agent_id or stream_meta.get("agent_id")
        if agent_id is not None:
            payload["agent_id"] = agent_id
        self._schedule_broadcast("actor_state", payload)

    def _on_cycle(self, event: Any) -> None:
        self._schedule_broadcast("cycle_finished", {
            "event": event.event,
            "channel": "system",
            "source_kind": "system",
            "source_label": "Router",
            "summary": f"Cycle finished · {event.event}",
        })

    def _on_supervisor(self, event: Any) -> None:
        self._schedule_broadcast("supervisor_message", {
            "channel": "supervisor",
            "agent_id": "kastor",
            "source_kind": "agent",
            "source_label": "Kastor",
            "stage": event.stage,
            "reason_code": event.reason_code,
            "notes": event.notes,
            "answer": event.answer,
            "summary": event.notes or event.answer or event.reason_code or event.stage,
        })

    def _on_error(self, event: Any) -> None:
        self._schedule_broadcast("error", {
            "channel": "system",
            "source_kind": "system",
            "source_label": "System",
            "message": event.message,
            "summary": event.message,
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

    def _infer_agent_id(
        self,
        *,
        panel: str | None = None,
        actor: str | None = None,
    ) -> str | None:
        return infer_agent_id(panel=panel, actor=actor)

    def _stream_meta_for_panel(self, panel: str | None) -> dict[str, Any]:
        return stream_meta_for_panel(panel)

    def _stream_meta_for_actor(self, actor: str | None) -> dict[str, Any]:
        return stream_meta_for_actor(actor)
