"""WebSocket EventHub — broadcasts events to all connected clients.

Supports two connection types:
* **Global** — receives every event (``/ws/events``).
* **Per-agent** — receives only events whose ``agent_id`` field matches
  a specific agent (``/ws/agent/{agent_id}``).

Includes server-side heartbeat: sends ``{"type":"ping"}`` every 30 s
and disconnects unresponsive clients after 90 s.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from amiagi.interfaces.web.stream_contract import normalize_stream_payload

logger = logging.getLogger(__name__)

# Heartbeat settings
_HEARTBEAT_INTERVAL_S = 30
_HEARTBEAT_TIMEOUT_S = 90


class EventHub:
    """Central WebSocket hub: broadcast events to connected clients.

    Each connected client is stored in ``_connections`` (global) or
    ``_agent_listeners`` (per-agent).  ``broadcast`` fans messages out
    to global clients *and* matching per-agent listeners.
    """

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._agent_listeners: dict[str, list[WebSocket]] = defaultdict(list)
        self._last_seen: dict[int, float] = {}  # id(ws) → monotonic time
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._recent_events: list[dict[str, Any]] = []
        self._recent_limit = 200
        self._next_event_id = 1

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def start_heartbeat(self) -> None:
        """Launch the background heartbeat coroutine."""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())
            logger.info("EventHub heartbeat started (interval=%ds, timeout=%ds)",
                        _HEARTBEAT_INTERVAL_S, _HEARTBEAT_TIMEOUT_S)

    def stop_heartbeat(self) -> None:
        """Cancel the heartbeat background task."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()

    async def _heartbeat_loop(self) -> None:
        """Periodically send ping and prune stale connections."""
        ping_msg = json.dumps({"type": "ping"})
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
            now = time.monotonic()
            stale: list[WebSocket] = []
            for ws in list(self._connections):
                ws_id = id(ws)
                last = self._last_seen.get(ws_id, now)
                if now - last > _HEARTBEAT_TIMEOUT_S:
                    stale.append(ws)
                    continue
                try:
                    await ws.send_text(ping_msg)
                except Exception:
                    stale.append(ws)
            for ws in stale:
                await self.disconnect(ws)
                logger.info("Heartbeat pruned stale connection %s", ws.client)

    def mark_alive(self, websocket: WebSocket) -> None:
        """Record that *websocket* sent data (resets heartbeat timer)."""
        self._last_seen[id(websocket)] = time.monotonic()

    # ------------------------------------------------------------------
    # Global connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a global WebSocket connection."""
        await websocket.accept()
        self._connections.append(websocket)
        self._last_seen[id(websocket)] = time.monotonic()
        logger.info(
            "WebSocket connected (total=%d, client=%s)",
            len(self._connections),
            websocket.client,
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from the global connections list."""
        try:
            self._connections.remove(websocket)
        except ValueError:
            pass
        self._last_seen.pop(id(websocket), None)
        logger.info(
            "WebSocket disconnected (total=%d, client=%s)",
            len(self._connections),
            websocket.client,
        )

    # ------------------------------------------------------------------
    # Per-agent listeners
    # ------------------------------------------------------------------

    def register_agent_listener(self, agent_id: str, websocket: WebSocket) -> None:
        """Register a WebSocket that only receives events for *agent_id*."""
        self._agent_listeners[agent_id].append(websocket)
        logger.info(
            "Agent listener registered (%s, total=%d)",
            agent_id,
            len(self._agent_listeners[agent_id]),
        )

    def unregister_agent_listener(self, agent_id: str, websocket: WebSocket) -> None:
        """Remove a per-agent listener."""
        listeners = self._agent_listeners.get(agent_id, [])
        try:
            listeners.remove(websocket)
        except ValueError:
            pass
        if not listeners:
            self._agent_listeners.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def broadcast(self, event_type: str, payload: dict[str, Any], *, panel: str | None = None) -> None:
        """Send a JSON message to global clients and matching agent listeners.

        Args:
            event_type: Event type string (e.g. ``agent.update``).
            payload: Additional data merged into the message.
            panel: Optional UI panel identifier for client-side filtering.
        """
        msg_dict: dict[str, Any] = {"type": event_type, **payload}
        if panel is not None:
            msg_dict["panel"] = panel
        msg_dict.setdefault("ts", datetime.now(timezone.utc).isoformat())
        msg_dict = normalize_stream_payload(event_type, msg_dict)
        msg_dict.setdefault("event_id", self._next_event_id)
        self._next_event_id = int(msg_dict["event_id"]) + 1
        self._recent_events.append(dict(msg_dict))
        if len(self._recent_events) > self._recent_limit:
            self._recent_events = self._recent_events[-self._recent_limit:]
        message = json.dumps(msg_dict)

        # 1. Global broadcast
        stale: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except (WebSocketDisconnect, RuntimeError, Exception):
                stale.append(ws)
        for ws in stale:
            await self.disconnect(ws)

        # 2. Per-agent broadcast (if payload contains agent_id)
        agent_id = payload.get("agent_id")
        if agent_id and agent_id in self._agent_listeners:
            agent_stale: list[WebSocket] = []
            for ws in self._agent_listeners[agent_id]:
                try:
                    await ws.send_text(message)
                except (WebSocketDisconnect, RuntimeError, Exception):
                    agent_stale.append(ws)
            for ws in agent_stale:
                self.unregister_agent_listener(agent_id, ws)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def agent_listener_count(self) -> int:
        return sum(len(v) for v in self._agent_listeners.values())

    def get_recent_events(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is None or limit <= 0:
            return list(self._recent_events)
        return list(self._recent_events[-limit:])

    def get_events_after(self, event_id: int, *, limit: int | None = None) -> tuple[list[dict[str, Any]], bool]:
        if event_id < 0:
            event_id = 0
        events = self.get_recent_events(limit=limit)
        if not events:
            return [], False

        oldest_event_id = int(events[0].get("event_id") or 0)
        truncated = event_id > 0 and event_id < (oldest_event_id - 1)
        if truncated:
            return events, True

        filtered = [event for event in events if int(event.get("event_id") or 0) > event_id]
        return filtered, False
