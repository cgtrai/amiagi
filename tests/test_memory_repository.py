from __future__ import annotations

from pathlib import Path

from amiagi.infrastructure.memory_repository import MemoryRepository


def test_messages_and_memories_are_persisted(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    repository = MemoryRepository(db_path)

    repository.append_message("user", "hello")
    repository.append_message("assistant", "hi")
    repository.add_memory(kind="note", content="ważna notatka", source="manual")

    reloaded = MemoryRepository(db_path)
    messages = reloaded.recent_messages(limit=10)
    memories = reloaded.search_memories(limit=10)

    assert [msg.role for msg in messages] == ["user", "assistant"]
    assert memories[0].content == "ważna notatka"


def test_search_memories_with_query(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.add_memory(kind="note", content="python asyncio", source="manual")
    repository.add_memory(kind="note", content="docker compose", source="manual")

    result = repository.search_memories(query="python", limit=10)

    assert len(result) == 1
    assert result[0].content == "python asyncio"


def test_latest_memory_returns_newest_by_kind_and_source(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.add_memory(kind="session_summary", content="stare", source="session_end")
    repository.add_memory(kind="session_summary", content="nowe", source="session_end")

    latest = repository.latest_memory(kind="session_summary", source="session_end")

    assert latest is not None
    assert latest.content == "nowe"


def test_clear_all_removes_messages_and_memories(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.db")
    repository.append_message("user", "hello")
    repository.add_memory(kind="note", content="abc", source="manual")

    repository.clear_all()

    assert repository.recent_messages(limit=10) == []
    assert repository.search_memories(limit=10) == []
