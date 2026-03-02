"""ContextWindowManager — build optimised context for model calls."""

from __future__ import annotations

from typing import Any

from amiagi.application.context_compressor import ContextCompressor
from amiagi.application.cross_agent_memory import CrossAgentMemory


class ContextWindowManager:
    """Assemble a context payload that fits within the model's token window.

    Priority order (highest → lowest):
    1. System prompt
    2. Skills text
    3. Cross-agent memory (relevant findings)
    4. Task description
    5. Conversation history (compressed if needed)
    """

    def __init__(
        self,
        *,
        max_tokens: int = 8000,
        compressor: ContextCompressor | None = None,
        cross_memory: CrossAgentMemory | None = None,
    ) -> None:
        self._max_tokens = max_tokens
        self._compressor = compressor or ContextCompressor()
        self._cross_memory = cross_memory

    def build_context(
        self,
        *,
        system_prompt: str = "",
        skills_text: str = "",
        task_description: str = "",
        task_id: str | None = None,
        task_tags: list[str] | None = None,
        conversation: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """Return a ``messages`` list ready to send to the model.

        Each section is measured and, if necessary, the conversation
        history is compressed to fit within ``max_tokens``.
        """
        char_budget = self._max_tokens * 4  # rough token→char estimate

        messages: list[dict[str, str]] = []
        used = 0

        # 1. System prompt
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
            used += len(system_prompt)

        # 2. Skills
        if skills_text:
            messages.append({"role": "system", "content": skills_text})
            used += len(skills_text)

        # 3. Cross-agent memory
        if self._cross_memory is not None:
            memory_text = self._cross_memory.relevant_context(
                task_id=task_id,
                tags=task_tags,
                limit=5,
            )
            if memory_text:
                messages.append({"role": "system", "content": memory_text})
                used += len(memory_text)

        # 4. Task description
        if task_description:
            messages.append({"role": "system", "content": f"[Current task]\n{task_description}"})
            used += len(task_description) + 15

        # 5. Conversation history (compress to fit remaining budget)
        if conversation:
            remaining = max(char_budget - used, 400)
            remaining_tokens = remaining // 4
            compressed = self._compressor.compress(
                conversation,
                max_tokens=remaining_tokens,
            )
            messages.extend(compressed)

        return messages

    @property
    def max_tokens(self) -> int:
        return self._max_tokens
