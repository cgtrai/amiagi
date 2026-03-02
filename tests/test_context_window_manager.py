"""Tests for ContextWindowManager (Phase 5)."""

from __future__ import annotations

import pytest

from amiagi.application.context_compressor import ContextCompressor
from amiagi.application.context_window_manager import ContextWindowManager
from amiagi.application.cross_agent_memory import CrossAgentMemory, MemoryItem


class TestContextWindowManager:
    def test_build_with_system_prompt_only(self) -> None:
        mgr = ContextWindowManager(max_tokens=8000)
        result = mgr.build_context(system_prompt="You are a helper.")
        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert "helper" in result[0]["content"]

    def test_build_with_all_sections(self) -> None:
        memory = CrossAgentMemory()
        memory.store(MemoryItem(agent_id="a1", task_id="t1", key_findings="important finding"))
        mgr = ContextWindowManager(
            max_tokens=8000,
            compressor=ContextCompressor(),
            cross_memory=memory,
        )
        result = mgr.build_context(
            system_prompt="system",
            skills_text="skill info",
            task_description="do the thing",
            task_id="t1",
            conversation=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi back"},
            ],
        )
        # Should have: system + skills + memory + task + 2 conversation msgs
        assert len(result) >= 5
        roles = [m["role"] for m in result]
        assert roles.count("system") >= 3  # system + skills + memory + task

    def test_build_without_memory(self) -> None:
        mgr = ContextWindowManager(max_tokens=8000)
        result = mgr.build_context(
            system_prompt="s",
            task_description="t",
            conversation=[{"role": "user", "content": "q"}],
        )
        assert len(result) >= 2

    def test_conversation_compressed_when_needed(self) -> None:
        mgr = ContextWindowManager(max_tokens=10)  # very small budget
        long_conv = [
            {"role": "user", "content": "x" * 500},
            {"role": "assistant", "content": "y" * 500},
        ] * 10
        result = mgr.build_context(conversation=long_conv)
        # The compressor should reduce messages
        assert len(result) < len(long_conv)

    def test_max_tokens_property(self) -> None:
        mgr = ContextWindowManager(max_tokens=4096)
        assert mgr.max_tokens == 4096

    def test_empty_build(self) -> None:
        mgr = ContextWindowManager()
        result = mgr.build_context()
        assert result == []

    def test_task_description_label(self) -> None:
        mgr = ContextWindowManager()
        result = mgr.build_context(task_description="write a test")
        assert len(result) == 1
        assert "[Current task]" in result[0]["content"]
