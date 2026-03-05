"""Webhook dispatcher — manages and fires user-configured webhooks.

Table: ``dbo.webhooks`` — user-managed webhook endpoints.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class WebhookRecord:
    id: str
    user_id: str
    url: str
    events: list[str]
    secret: str | None
    is_active: bool
    created_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "url": self.url,
            "events": self.events,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _row_to_webhook(row) -> WebhookRecord:
    return WebhookRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        url=row["url"],
        events=list(row["events"] or []),
        secret=row.get("secret"),
        is_active=row["is_active"],
        created_at=row.get("created_at"),
    )


def compute_signature(secret: str, payload: str) -> str:
    """HMAC-SHA256 signature for webhook payload."""
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


class WebhookManager:
    """CRUD and dispatch for user webhooks."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def create_webhook(
        self, user_id: str, url: str, events: list[str],
        secret: str | None = None,
    ) -> WebhookRecord:
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.webhooks (user_id, url, events, secret)
            VALUES ($1::uuid, $2, $3, $4)
            RETURNING *
            """,
            user_id, url, events, secret,
        )
        return _row_to_webhook(row)

    async def list_webhooks(self, user_id: str) -> list[WebhookRecord]:
        rows = await self._pool.fetch(
            "SELECT * FROM dbo.webhooks WHERE user_id = $1::uuid ORDER BY created_at DESC",
            user_id,
        )
        return [_row_to_webhook(r) for r in rows]

    async def delete_webhook(self, webhook_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM dbo.webhooks WHERE id = $1::uuid", webhook_id
        )
        return result.endswith("1")

    async def get_active_webhooks_for_event(self, event_type: str) -> list[WebhookRecord]:
        """Get all active webhooks that subscribe to a given event type."""
        rows = await self._pool.fetch(
            "SELECT * FROM dbo.webhooks WHERE is_active = true AND $1 = ANY(events)",
            event_type,
        )
        return [_row_to_webhook(r) for r in rows]

    async def dispatch(
        self, event_type: str, payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Dispatch webhook to all subscribers. Returns delivery results."""
        hooks = await self.get_active_webhooks_for_event(event_type)
        results = []
        for hook in hooks:
            body = json.dumps({"event": event_type, "data": payload})
            headers = {"Content-Type": "application/json"}
            if hook.secret:
                headers["X-Webhook-Signature"] = compute_signature(hook.secret, body)
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(hook.url, content=body, headers=headers)
                results.append({"webhook_id": hook.id, "status": resp.status_code, "ok": resp.is_success})
            except Exception as exc:
                results.append({"webhook_id": hook.id, "status": 0, "ok": False, "error": str(exc)})
        return results
