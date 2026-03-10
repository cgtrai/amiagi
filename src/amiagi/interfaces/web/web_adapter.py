"""WebAdapter — bridges EventBus events to WebSocket clients.

Subscribes to all EventBus event types and broadcasts serialised JSON
to connected WebSocket clients via the EventHub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from amiagi.application.communication_protocol import (
    is_sponsor_readable,
    parse_addressed_blocks,
    strip_tool_call_blocks,
)
from amiagi.application.tool_calling import parse_tool_calls
from amiagi.interfaces.web.stream_contract import (
    agent_thread_owner,
    infer_agent_id,
    normalize_stream_payload,
    routing_for_actor,
    routing_for_panel,
    routing_for_supervisor_report,
    routing_for_system_event,
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

_SUPERVISOR_SUMMARY_DEDUP_WINDOW_S = 15.0


def _tool_call_summary(answer: str) -> str:
    calls = parse_tool_calls(answer or "")
    if not calls:
        return ""
    first = calls[0]
    intent = str(first.intent or "krok operacyjny").strip() or "krok operacyjny"
    return f"Kastor zasugerował krok: {first.tool} ({intent})"


def _sponsor_visible_text(answer: str) -> str:
    cleaned_answer = str(answer or "").strip()
    if not cleaned_answer:
        return ""

    blocks = parse_addressed_blocks(cleaned_answer)
    addressed_blocks = [block for block in blocks if block.sender and block.target]
    if addressed_blocks:
        for block in addressed_blocks:
            if block.target not in {"Sponsor", "all"}:
                continue
            cleaned = strip_tool_call_blocks(block.content)
            if cleaned and is_sponsor_readable(cleaned):
                return cleaned
        return ""

    cleaned = strip_tool_call_blocks(cleaned_answer)
    if cleaned and is_sponsor_readable(cleaned):
        return cleaned
    return ""


def _internal_supervisor_text(notes: str, answer: str) -> str:
    cleaned_notes = str(notes or "").strip()
    if cleaned_notes:
        return cleaned_notes

    cleaned_answer = str(answer or "").strip()
    if not cleaned_answer:
        return ""

    blocks = parse_addressed_blocks(cleaned_answer)
    addressed_blocks = [block for block in blocks if block.sender and block.target]
    if addressed_blocks:
        internal_chunks: list[str] = []
        for block in addressed_blocks:
            if block.target in {"Sponsor", "all"}:
                continue
            cleaned = strip_tool_call_blocks(block.content)
            internal_chunks.append(cleaned or block.content.strip())
        return "\n\n".join(chunk for chunk in internal_chunks if chunk).strip()

    cleaned = strip_tool_call_blocks(cleaned_answer)
    if cleaned and cleaned != cleaned_answer:
        return cleaned
    return _tool_call_summary(cleaned_answer)



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
        self._last_supervisor_summary_by_target: dict[str, tuple[str, float]] = {}

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
        payload.update(routing_for_panel(event.panel, agent_id=agent_id))
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
        payload.update(routing_for_actor(event.actor, agent_id=agent_id))
        self._schedule_broadcast("actor_state", payload)

    def _on_cycle(self, event: Any) -> None:
        payload = {
            "event": event.event,
            "channel": "system",
            "source_kind": "system",
            "source_label": "Router",
            "summary": f"Cycle finished · {event.event}",
        }
        payload.update(routing_for_system_event(agent_id="router"))
        self._schedule_broadcast("cycle_finished", payload)

    def _on_supervisor(self, event: Any) -> None:
        sponsor_text = _sponsor_visible_text(event.answer)
        internal_text = _internal_supervisor_text(event.notes, event.answer)

        if internal_text and not self._is_duplicate_supervisor_summary("polluks", internal_text):
            internal_payload = {
                "channel": "supervisor",
                "agent_id": "kastor",
                "source_kind": "agent",
                "source_label": "Kastor",
                "stage": event.stage,
                "reason_code": event.reason_code,
                "notes": event.notes,
                "answer": event.answer,
                "summary": internal_text,
                "target_agent": "polluks",
                "to": "Polluks",
                "thread_owners": [agent_thread_owner("polluks")],
                "direction_per_owner": {agent_thread_owner("polluks"): "incoming"},
            }
            self._schedule_broadcast("supervisor_message", internal_payload)

        if sponsor_text and not self._is_duplicate_supervisor_summary("sponsor", sponsor_text):
            report_payload = {
                "channel": "supervisor",
                "agent_id": "kastor",
                "source_kind": "agent",
                "source_label": "Kastor",
                "stage": event.stage,
                "reason_code": event.reason_code,
                "notes": event.notes,
                "answer": event.answer,
                "summary": sponsor_text,
                "to": "Sponsor",
            }
            report_payload.update(routing_for_supervisor_report("kastor"))
            self._schedule_broadcast("supervisor_message", report_payload)
            return

        if internal_text:
            return

        fallback_payload = {
            "channel": "supervisor",
            "agent_id": "kastor",
            "source_kind": "agent",
            "source_label": "Kastor",
            "stage": event.stage,
            "reason_code": event.reason_code,
            "notes": event.notes,
            "answer": event.answer,
            "summary": event.reason_code or event.stage,
        }
        fallback_payload.update(routing_for_supervisor_report("kastor"))
        self._schedule_broadcast("supervisor_message", fallback_payload)

    def _is_duplicate_supervisor_summary(self, target: str, summary: str) -> bool:
        normalized_target = str(target or "").strip().lower() or "unknown"
        normalized_summary = str(summary or "").strip()
        if not normalized_summary:
            return False

        now = time.monotonic()
        last = self._last_supervisor_summary_by_target.get(normalized_target)
        if last is not None:
            last_summary, last_ts = last
            if last_summary == normalized_summary and now - last_ts <= _SUPERVISOR_SUMMARY_DEDUP_WINDOW_S:
                return True

        self._last_supervisor_summary_by_target[normalized_target] = (normalized_summary, now)
        return False

    def _on_error(self, event: Any) -> None:
        payload = {
            "channel": "system",
            "source_kind": "system",
            "source_label": "System",
            "message": event.message,
            "summary": event.message,
        }
        payload.update(routing_for_system_event())
        self._schedule_broadcast("error", payload)

    # ------------------------------------------------------------------
    # Thread-safe broadcast helper
    # ------------------------------------------------------------------

    def _schedule_broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        """Schedule an async broadcast from the synchronous EventBus thread."""
        if self._event_hub is None or self._loop is None:
            return
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        payload = normalize_stream_payload(event_type, payload)
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
