"""AskHumanTool & ReviewRequestTool — HITL tools for agent → operator communication.

These tools allow agents to request human input during task execution.
They integrate with the InboxService to create pending inbox items
that operators can respond to from the web UI.

The tools are designed to be called from RouterEngine.execute_tool_call()
and produce inbox items that block agent execution until resolved.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from amiagi.interfaces.web.monitoring.inbox_service import InboxService

logger = logging.getLogger(__name__)


@dataclass
class HumanToolResult:
    """Result of an AskHuman or ReviewRequest tool call."""
    inbox_item_id: str
    status: str  # "pending", "submitted", "error"
    message: str
    item_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.status != "error",
            "tool": self.item_type,
            "inbox_item_id": self.inbox_item_id,
            "status": self.status,
            "message": self.message,
        }


class HumanInteractionBridge:
    """Bridge between RouterEngine tool calls and InboxService.

    Instantiated once and stored on app.state. The RouterEngine
    calls ``ask_human()`` or ``request_review()`` synchronously,
    and this bridge creates an async inbox item via
    ``asyncio.run_coroutine_threadsafe()``.
    """

    def __init__(
        self,
        inbox_service: "InboxService",
        event_hub: Any = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._inbox = inbox_service
        self._hub = event_hub
        self._loop = loop

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def set_event_hub(self, hub: Any) -> None:
        self._hub = hub

    # ── ask_human ─────────────────────────────────────────────

    def ask_human(
        self,
        *,
        question: str,
        agent_id: str = "",
        context: str = "",
        priority: int = 5,
    ) -> dict[str, Any]:
        """Create an inbox item asking the human operator a question.

        Returns a tool result dict for the RouterEngine.
        """
        if not self._loop or not self._inbox:
            return {
                "ok": False,
                "tool": "ask_human",
                "error": "InboxService not available",
            }

        item_id = str(uuid.uuid4())
        body = question
        if context:
            body += f"\n\n--- Context ---\n{context}"

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._inbox.create(
                    item_type="ask_human",
                    title=f"Question from agent",
                    body=body,
                    source_type="tool",
                    source_id=item_id,
                    agent_id=agent_id or None,
                    priority=priority,
                    metadata={
                        "question": question,
                        "context": context,
                        "agent_id": agent_id,
                    },
                ),
                self._loop,
            )
            # Wait briefly for creation (non-blocking to the engine)
            inbox_item = future.result(timeout=5.0)
            real_id = inbox_item.id if inbox_item else item_id

            # Broadcast event to UI
            self._broadcast_inbox_event(real_id, "ask_human", agent_id)

            return {
                "ok": True,
                "tool": "ask_human",
                "inbox_item_id": real_id,
                "status": "pending",
                "message": (
                    "Your question has been submitted to the operator's inbox. "
                    "The operator will respond when available. "
                    "You may continue with other work or wait for the response."
                ),
            }
        except Exception as e:
            logger.warning("ask_human failed: %s", e, exc_info=True)
            return {
                "ok": False,
                "tool": "ask_human",
                "error": f"Failed to submit question: {e}",
            }

    # ── review_request ────────────────────────────────────────

    def request_review(
        self,
        *,
        title: str = "Code review requested",
        description: str = "",
        content: str = "",
        agent_id: str = "",
        priority: int = 3,
    ) -> dict[str, Any]:
        """Create an inbox item requesting human review of work.

        Returns a tool result dict for the RouterEngine.
        """
        if not self._loop or not self._inbox:
            return {
                "ok": False,
                "tool": "review_request",
                "error": "InboxService not available",
            }

        item_id = str(uuid.uuid4())
        body = description
        if content:
            body += f"\n\n--- Content for Review ---\n{content}"

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._inbox.create(
                    item_type="review_request",
                    title=title[:200],
                    body=body,
                    source_type="tool",
                    source_id=item_id,
                    agent_id=agent_id or None,
                    priority=priority,
                    metadata={
                        "title": title,
                        "description": description,
                        "content_preview": content[:500] if content else "",
                        "agent_id": agent_id,
                    },
                ),
                self._loop,
            )
            inbox_item = future.result(timeout=5.0)
            real_id = inbox_item.id if inbox_item else item_id

            self._broadcast_inbox_event(real_id, "review_request", agent_id)

            return {
                "ok": True,
                "tool": "review_request",
                "inbox_item_id": real_id,
                "status": "pending",
                "message": (
                    "Your review request has been submitted to the operator's inbox. "
                    "The operator will review your work when available."
                ),
            }
        except Exception as e:
            logger.warning("review_request failed: %s", e, exc_info=True)
            return {
                "ok": False,
                "tool": "review_request",
                "error": f"Failed to submit review request: {e}",
            }

    # ── Private helpers ───────────────────────────────────────

    def _broadcast_inbox_event(
        self, item_id: str, item_type: str, agent_id: str
    ) -> None:
        """Send a real-time event to connected WebSocket clients."""
        if self._hub is None or self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._hub.broadcast("inbox.new", {
                    "inbox_item_id": item_id,
                    "item_type": item_type,
                    "agent_id": agent_id,
                }),
                self._loop,
            )
        except Exception:
            logger.debug("Failed to broadcast inbox event", exc_info=True)
