"""Tests for CrossAgentMemory (Phase 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amiagi.application.cross_agent_memory import CrossAgentMemory, MemoryItem


@pytest.fixture()
def memory() -> CrossAgentMemory:
    return CrossAgentMemory()


@pytest.fixture()
def persisted_memory(tmp_path: Path) -> CrossAgentMemory:
    return CrossAgentMemory(persist_path=tmp_path / "mem.jsonl")


class TestCrossAgentMemory:
    def test_store_and_count(self, memory: CrossAgentMemory) -> None:
        assert memory.count() == 0
        memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="found X"))
        assert memory.count() == 1

    def test_query_by_agent_id(self, memory: CrossAgentMemory) -> None:
        memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="f1"))
        memory.store(MemoryItem(agent_id="a2", task_id="t1", key_findings="f2"))
        result = memory.query(agent_id="a1")
        assert len(result) == 1
        assert result[0].agent_id == "a1"

    def test_query_by_task_id(self, memory: CrossAgentMemory) -> None:
        memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="f1"))
        memory.store(MemoryItem(agent_id="a1", task_id="t2", key_findings="f2"))
        result = memory.query(task_id="t2")
        assert len(result) == 1
        assert result[0].task_id == "t2"

    def test_query_by_tags(self, memory: CrossAgentMemory) -> None:
        memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="f1", tags=["python"]))
        memory.store(MemoryItem(agent_id="a1", task_id="t2", key_findings="f2", tags=["java"]))
        result = memory.query(tags=["python"])
        assert len(result) == 1
        assert "python" in result[0].tags

    def test_query_limit(self, memory: CrossAgentMemory) -> None:
        for i in range(10):
            memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings=f"f{i}"))
        result = memory.query(limit=3)
        assert len(result) == 3

    def test_query_newest_first(self, memory: CrossAgentMemory) -> None:
        memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="old", timestamp=1.0))
        memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="new", timestamp=2.0))
        result = memory.query()
        assert result[0].key_findings == "new"

    def test_relevant_context_format(self, memory: CrossAgentMemory) -> None:
        memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="discovered bug"))
        ctx = memory.relevant_context(task_id="t1")
        assert "discovered bug" in ctx
        assert "Cross-agent" in ctx

    def test_relevant_context_empty(self, memory: CrossAgentMemory) -> None:
        assert memory.relevant_context(task_id="none") == ""

    def test_clear(self, memory: CrossAgentMemory) -> None:
        memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="f"))
        memory.clear()
        assert memory.count() == 0

    def test_persistence_write(self, persisted_memory: CrossAgentMemory, tmp_path: Path) -> None:
        persisted_memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="persistent"))
        log = (tmp_path / "mem.jsonl").read_text()
        data = json.loads(log.strip())
        assert data["key_findings"] == "persistent"

    def test_persistence_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "mem.jsonl"
        mem1 = CrossAgentMemory(persist_path=path)
        mem1.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="restored"))
        # Simulate restart
        mem2 = CrossAgentMemory(persist_path=path)
        assert mem2.count() == 1
        assert mem2.query()[0].key_findings == "restored"
