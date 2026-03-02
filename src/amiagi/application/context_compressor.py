"""ContextCompressor — summarise conversation history to fit model context window."""

from __future__ import annotations

from typing import Any, Protocol


class _ChatClient(Protocol):
    def chat(self, *, messages: list[dict[str, str]], system_prompt: str) -> str: ...


_COMPRESS_SYSTEM = (
    "You are a precise summariser. Condense the following conversation into "
    "the most important facts, decisions and action items. "
    "Output ONLY the summary, no preamble."
)


class ContextCompressor:
    """Compresses a message list using either an LLM or a simple heuristic.

    When no *client* is provided the compressor falls back to a heuristic
    that keeps only the first and last messages and truncates the middle.
    """

    def __init__(self, client: _ChatClient | None = None) -> None:
        self._client = client

    def compress(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2000,
    ) -> list[dict[str, str]]:
        """Return a shorter message list that fits within *max_tokens*.

        ``max_tokens`` is an *approximate* character budget (1 token ≈ 4 chars).
        """
        char_budget = max_tokens * 4
        total = sum(len(m.get("content", "")) for m in messages)

        if total <= char_budget:
            return list(messages)

        if self._client is not None:
            return self._compress_with_llm(messages, char_budget)

        return self._compress_heuristic(messages, char_budget)

    # ---- LLM path ----

    def _compress_with_llm(
        self,
        messages: list[dict[str, str]],
        char_budget: int,
    ) -> list[dict[str, str]]:
        assert self._client is not None
        conversation = "\n".join(
            f"{m.get('role', 'unknown')}: {m.get('content', '')}"
            for m in messages
        )
        try:
            summary = self._client.chat(
                messages=[{"role": "user", "content": conversation}],
                system_prompt=_COMPRESS_SYSTEM,
            )
            return [{"role": "system", "content": f"[Summary of prior conversation]\n{summary}"}]
        except Exception:
            return self._compress_heuristic(messages, char_budget)

    # ---- heuristic path ----

    @staticmethod
    def _compress_heuristic(
        messages: list[dict[str, str]],
        char_budget: int,
    ) -> list[dict[str, str]]:
        """Keep first message, system messages, and as many recent messages as fit."""
        if not messages:
            return []

        result: list[dict[str, str]] = []
        used = 0

        # Always keep the first (system) message
        first = messages[0]
        first_len = len(first.get("content", ""))
        result.append(first)
        used += first_len

        # Collect remaining messages from newest to oldest
        remaining = messages[1:]
        tail: list[dict[str, str]] = []
        for msg in reversed(remaining):
            msg_len = len(msg.get("content", ""))
            if used + msg_len <= char_budget:
                tail.append(msg)
                used += msg_len
            else:
                break

        tail.reverse()

        if len(tail) < len(remaining):
            result.append({
                "role": "system",
                "content": f"[{len(remaining) - len(tail)} earlier messages compressed]",
            })

        result.extend(tail)
        return result
