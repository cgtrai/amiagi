"""Tests for ContextCompressor (Phase 5)."""

from __future__ import annotations

import pytest

from amiagi.application.context_compressor import ContextCompressor


class _FakeChatClient:
    """Stub chat client that returns a canned summary."""

    def chat(self, *, messages: list[dict[str, str]], system_prompt: str) -> str:
        return "Summary: The conversation covered topics A and B."


class _FailingChatClient:
    """Stub chat client that always raises."""

    def chat(self, *, messages: list[dict[str, str]], system_prompt: str) -> str:
        raise RuntimeError("API timeout")


def _make_messages(n: int, content_len: int = 100) -> list[dict[str, str]]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i} " + "x" * content_len}
        for i in range(n)
    ]


class TestContextCompressor:
    def test_short_conversation_unchanged(self) -> None:
        comp = ContextCompressor()
        msgs = [{"role": "user", "content": "hello"}]
        result = comp.compress(msgs, max_tokens=1000)
        assert result == msgs

    def test_heuristic_keeps_first_message(self) -> None:
        comp = ContextCompressor()
        msgs = _make_messages(20, content_len=200)
        result = comp.compress(msgs, max_tokens=5)  # very tight budget
        assert result[0] == msgs[0]

    def test_heuristic_adds_compression_notice(self) -> None:
        comp = ContextCompressor()
        msgs = _make_messages(20, content_len=200)
        result = comp.compress(msgs, max_tokens=10)
        # Should have a "[X earlier messages compressed]" system message
        system_msgs = [m for m in result if m["role"] == "system"]
        assert any("compressed" in m["content"].lower() for m in system_msgs)

    def test_llm_path_returns_summary(self) -> None:
        comp = ContextCompressor(client=_FakeChatClient())
        msgs = _make_messages(20, content_len=200)
        result = comp.compress(msgs, max_tokens=5)
        assert len(result) == 1
        assert "Summary" in result[0]["content"]

    def test_llm_path_fallback_on_error(self) -> None:
        comp = ContextCompressor(client=_FailingChatClient())
        msgs = _make_messages(20, content_len=200)
        result = comp.compress(msgs, max_tokens=10)
        # Should fall back to heuristic
        assert len(result) > 0
        assert result[0] == msgs[0]

    def test_empty_messages(self) -> None:
        comp = ContextCompressor()
        result = comp.compress([], max_tokens=1000)
        assert result == []

    def test_within_budget_returns_copy(self) -> None:
        comp = ContextCompressor()
        msgs = [{"role": "user", "content": "short"}]
        result = comp.compress(msgs, max_tokens=1000)
        assert result is not msgs  # must be a copy
        assert result == msgs
