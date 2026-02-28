from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from amiagi.domain.models import MemoryRecord, Message


class MemoryRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS memory_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_created_at
                ON messages(created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_memory_records_created_at
                ON memory_records(created_at DESC);
                """
            )
            self._migrate_add_actor_column(connection)

    def _migrate_add_actor_column(self, connection: sqlite3.Connection) -> None:
        """Add actor column to messages table if missing (backward compat)."""
        cursor = connection.execute("PRAGMA table_info(messages)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "actor" not in columns:
            connection.execute("ALTER TABLE messages ADD COLUMN actor TEXT NOT NULL DEFAULT ''")

    def append_message(self, role: str, content: str, *, actor: str = "") -> Message:
        created_at = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO messages(role, content, created_at, actor) VALUES (?, ?, ?, ?)",
                (role, content, created_at.isoformat(), actor),
            )
        return Message(role=role, content=content, created_at=created_at, actor=actor)

    def add_memory(self, kind: str, content: str, source: str = "manual") -> MemoryRecord:
        created_at = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO memory_records(kind, content, source, created_at) VALUES (?, ?, ?, ?)",
                (kind, content, source, created_at.isoformat()),
            )
        return MemoryRecord(kind=kind, content=content, source=source, created_at=created_at)

    def replace_memory(self, kind: str, source: str, content: str) -> MemoryRecord:
        created_at = datetime.utcnow()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM memory_records WHERE kind = ? AND source = ?",
                (kind, source),
            )
            connection.execute(
                "INSERT INTO memory_records(kind, content, source, created_at) VALUES (?, ?, ?, ?)",
                (kind, content, source, created_at.isoformat()),
            )
        return MemoryRecord(kind=kind, content=content, source=source, created_at=created_at)

    def recent_messages(self, limit: int = 20) -> list[Message]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, created_at, actor
                FROM messages
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        messages = [
            Message(
                role=row["role"],
                content=row["content"],
                created_at=datetime.fromisoformat(row["created_at"]),
                actor=row["actor"] if "actor" in row.keys() else "",
            )
            for row in rows
        ]
        messages.reverse()
        return messages

    def search_memories(self, query: str | None = None, limit: int = 20) -> list[MemoryRecord]:
        with self._connect() as connection:
            if query:
                rows = connection.execute(
                    """
                    SELECT kind, content, source, created_at
                    FROM memory_records
                    WHERE content LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (f"%{query}%", limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT kind, content, source, created_at
                    FROM memory_records
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

        return [
            MemoryRecord(
                kind=row["kind"],
                content=row["content"],
                source=row["source"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def latest_memory(self, kind: str, source: str | None = None) -> MemoryRecord | None:
        with self._connect() as connection:
            if source is None:
                row = connection.execute(
                    """
                    SELECT kind, content, source, created_at
                    FROM memory_records
                    WHERE kind = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (kind,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT kind, content, source, created_at
                    FROM memory_records
                    WHERE kind = ? AND source = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (kind, source),
                ).fetchone()

        if row is None:
            return None

        return MemoryRecord(
            kind=row["kind"],
            content=row["content"],
            source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def clear_all(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM messages")
            connection.execute("DELETE FROM memory_records")
