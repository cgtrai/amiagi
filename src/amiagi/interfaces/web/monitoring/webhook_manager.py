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
    last_delivery_status: int | None
    last_delivery_at: datetime | None
    last_error: str | None
    created_at: datetime | None

    @property
    def status(self) -> str:
        if not self.is_active:
            return "disabled"
        if self.last_delivery_status is None:
            return "active"
        if 200 <= int(self.last_delivery_status) < 300:
            return "active"
        return "failing"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "url": self.url,
            "events": self.events,
            "is_active": self.is_active,
            "status": self.status,
            "last_delivery_status": self.last_delivery_status,
            "last_delivery_at": self.last_delivery_at.isoformat() if self.last_delivery_at else None,
            "last_error": self.last_error,
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
        last_delivery_status=row.get("last_delivery_status"),
        last_delivery_at=row.get("last_delivery_at"),
        last_error=row.get("last_error"),
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
        is_active: bool = True,
    ) -> WebhookRecord:
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.webhooks (user_id, url, events, secret, is_active)
            VALUES ($1::uuid, $2, $3, $4, $5)
            RETURNING *
            """,
            user_id, url, events, secret, is_active,
        )
        return _row_to_webhook(row)

    async def get_webhook(self, webhook_id: str) -> WebhookRecord | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM dbo.webhooks WHERE id = $1::uuid",
            webhook_id,
        )
        return _row_to_webhook(row) if row else None

    async def update_webhook(
        self,
        webhook_id: str,
        *,
        url: str | None = None,
        events: list[str] | None = None,
        secret: str | None = None,
        is_active: bool | None = None,
    ) -> WebhookRecord | None:
        updates: list[str] = []
        params: list[Any] = []

        def add(value: Any, sql: str) -> None:
            params.append(value)
            updates.append(sql.format(index=len(params)))

        if url is not None:
            add(url, "url = ${index}")
        if events is not None:
            add(events, "events = ${index}")
        if secret is not None:
            add(secret, "secret = ${index}")
        if is_active is not None:
            add(is_active, "is_active = ${index}")

        if not updates:
            return await self.get_webhook(webhook_id)

        params.append(webhook_id)
        row = await self._pool.fetchrow(
            f"UPDATE dbo.webhooks SET {', '.join(updates)} WHERE id = ${len(params)}::uuid RETURNING *",
            *params,
        )
        return _row_to_webhook(row) if row else None

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

    async def record_delivery_result(
        self,
        webhook_id: str,
        *,
        status: int | None,
        error: str | None = None,
    ) -> None:
        await self._pool.execute(
            """
            UPDATE dbo.webhooks
            SET last_delivery_status = $1,
                last_delivery_at = now(),
                last_error = $2
            WHERE id = $3::uuid
            """,
            status,
            error,
            webhook_id,
        )

    async def deliver_webhook(
        self,
        hook: WebhookRecord,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = json.dumps({"event": event_type, "data": payload})
        headers = {"Content-Type": "application/json"}
        if hook.secret:
            headers["X-Webhook-Signature"] = compute_signature(hook.secret, body)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(hook.url, content=body, headers=headers)
            result = {"webhook_id": hook.id, "status": resp.status_code, "ok": resp.is_success}
            await self.record_delivery_result(hook.id, status=resp.status_code, error=None if resp.is_success else f"HTTP {resp.status_code}")
            return result
        except Exception as exc:
            await self.record_delivery_result(hook.id, status=0, error=str(exc))
            return {"webhook_id": hook.id, "status": 0, "ok": False, "error": str(exc)}

    async def dispatch(
        self, event_type: str, payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Dispatch webhook to all subscribers. Returns delivery results."""
        hooks = await self.get_active_webhooks_for_event(event_type)
        results = []
        for hook in hooks:
            results.append(await self.deliver_webhook(hook, event_type, payload))
        return results
