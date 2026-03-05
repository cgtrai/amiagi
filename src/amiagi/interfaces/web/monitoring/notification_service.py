"""Notification service — in-app notification center.

CRUD for ``dbo.notifications`` and ``dbo.notification_preferences``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class Notification:
    id: str
    user_id: str
    type: str
    title: str
    body: str
    is_read: bool
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "type": self.type,
            "title": self.title,
            "body": self.body,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def _row_to_notif(row) -> Notification:
    return Notification(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        type=row["type"],
        title=row["title"],
        body=row.get("body", ""),
        is_read=row["is_read"],
        created_at=row.get("created_at"),
    )


class NotificationService:
    """In-app notification management."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def create(
        self, user_id: str, type_: str, title: str, body: str = "",
    ) -> Notification:
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.notifications (user_id, type, title, body)
            VALUES ($1::uuid, $2, $3, $4) RETURNING *
            """,
            user_id, type_, title, body,
        )
        return _row_to_notif(row)

    async def list_for_user(
        self, user_id: str, *, unread_only: bool = False, limit: int = 50,
    ) -> list[Notification]:
        cond = "user_id = $1::uuid"
        if unread_only:
            cond += " AND is_read = false"
        rows = await self._pool.fetch(
            f"SELECT * FROM dbo.notifications WHERE {cond} ORDER BY created_at DESC LIMIT $2",
            user_id, limit,
        )
        return [_row_to_notif(r) for r in rows]

    async def unread_count(self, user_id: str) -> int:
        row = await self._pool.fetchrow(
            "SELECT count(*) AS cnt FROM dbo.notifications WHERE user_id = $1::uuid AND is_read = false",
            user_id,
        )
        return row["cnt"] if row else 0

    async def mark_read(self, notification_id: str) -> bool:
        result = await self._pool.execute(
            "UPDATE dbo.notifications SET is_read = true WHERE id = $1::uuid", notification_id
        )
        return "UPDATE 1" in result

    async def mark_all_read(self, user_id: str) -> int:
        result = await self._pool.execute(
            "UPDATE dbo.notifications SET is_read = true WHERE user_id = $1::uuid AND is_read = false",
            user_id,
        )
        # result like "UPDATE 5"
        parts = result.split()
        return int(parts[1]) if len(parts) > 1 else 0
