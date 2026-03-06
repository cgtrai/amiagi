"""Per-agent bidirectional WebSocket at ``/ws/agent/{agent_id}``.

Each connection subscribes to EventBus events filtered by *agent_id*
and relays user prompts to the RouterEngine.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from starlette.websockets import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from amiagi.interfaces.web.ws.event_hub import EventHub

logger = logging.getLogger(__name__)


class AgentStream:
    """Manages a single per-agent WebSocket connection.

    Subscribes to the global EventHub and filters messages by *agent_id*.
    Forwards user prompts to the RouterEngine via WebAdapter.
    """

    def __init__(
        self,
        websocket: WebSocket,
        agent_id: str,
    ) -> None:
        self._ws = websocket
        self._agent_id = agent_id
        self._closed = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Accept and serve the WebSocket until disconnection.

        Requires JWT auth via ``?token=`` query param.
        """
        app = self._ws.app

        # --- JWT auth ---
        # Prefer query-param token; fall back to HttpOnly session cookie
        # (the cookie is httponly so JS cannot read it, but the browser
        # sends it along with the WebSocket handshake request).
        token = self._ws.query_params.get("token") or ""
        if not token:
            token = self._ws.cookies.get("amiagi_session", "")
        if not token:
            await self._ws.close(code=4001, reason="Missing token")
            return

        session_mgr = getattr(app.state, "session_manager", None)
        if session_mgr is None:
            await self._ws.close(code=4001, reason="Auth unavailable")
            return

        session = await session_mgr.validate_session(token)
        if session is None:
            await self._ws.close(code=4001, reason="Invalid or expired token")
            return

        hub: EventHub = app.state.event_hub
        adapter = app.state.web_adapter

        await self._ws.accept()
        logger.info("AgentStream connected for %s (user=%s)", self._agent_id, session.email)

        # Register ourselves as a filtered subscriber
        hub.register_agent_listener(self._agent_id, self._ws)

        try:
            # Send initial agent state
            await self._send_initial_state(app)

            while True:
                data = await self._ws.receive_text()
                await self._handle_client_message(data, adapter)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("AgentStream error for %s", self._agent_id)
        finally:
            hub.unregister_agent_listener(self._agent_id, self._ws)
            self._closed = True
            logger.info("AgentStream disconnected for %s", self._agent_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _log_activity(self, action: str, detail: dict[str, Any] | None = None) -> None:
        """Fire-and-forget audit log entry via WebSocket context."""
        try:
            activity_logger = getattr(self._ws.app.state, "activity_logger", None)
            if activity_logger is None:
                return
            await activity_logger.log(
                user_id="ws-user",
                session_id=None,
                action=action,
                detail=detail,
                ip_address=self._ws.client.host if self._ws.client else None,
            )
        except Exception:
            logger.debug("Failed to log activity %s", action, exc_info=True)

    async def _send_initial_state(self, app: Any) -> None:
        """Push the current agent descriptor to the client on connect."""
        registry = getattr(app.state, "agent_registry", None)
        if registry is None:
            return

        descriptor = registry.get(self._agent_id)
        if descriptor is None:
            await self._ws.send_text(json.dumps({
                "type": "error",
                "message": f"Agent '{self._agent_id}' not found in registry.",
            }))
            return

        from amiagi.interfaces.web.routes.api_routes import _agent_to_dict

        await self._ws.send_text(json.dumps({
            "type": "agent_state",
            "agent": _agent_to_dict(descriptor),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))

    async def _handle_client_message(self, raw: str, adapter: Any) -> None:
        """Parse and dispatch a message from the client."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._ws.send_text(json.dumps({
                "type": "error",
                "message": "Invalid JSON",
            }))
            return

        msg_type = msg.get("type", "")

        if msg_type == "user_prompt":
            prompt = msg.get("message", "").strip()
            if prompt:
                adapter.submit_user_turn(prompt)
                await self._ws.send_text(json.dumps({
                    "type": "prompt_ack",
                    "agent_id": self._agent_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
                # Audit log: prompt.submit (server-side)
                await self._log_activity(
                    "prompt.submit",
                    {"agent_id": self._agent_id, "text": prompt[:500]},
                )
        elif msg_type == "ping":
            await self._ws.send_text(json.dumps({"type": "pong"}))
        else:
            await self._ws.send_text(json.dumps({
                "type": "error",
                "message": f"Unknown message type: {msg_type}",
            }))


# ------------------------------------------------------------------
# Starlette endpoint function
# ------------------------------------------------------------------

async def ws_agent_stream(websocket: WebSocket) -> None:
    """WebSocket endpoint: ``/ws/agent/{agent_id}``."""
    agent_id = websocket.path_params["agent_id"]
    stream = AgentStream(websocket, agent_id)
    await stream.run()
