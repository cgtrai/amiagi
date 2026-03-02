"""Unified model client protocol for amiagi.

Every LLM backend (Ollama, OpenAI, …) must satisfy this protocol so that
ChatService and SupervisorService can treat them interchangeably.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ChatCompletionClient(Protocol):
    """Structural-typing protocol for LLM chat backends."""

    @property
    def model(self) -> str: ...  # read-only; works with frozen & mutable dataclasses

    def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        num_ctx: int | None = None,
    ) -> str:
        """Send a chat completion request and return the assistant reply."""
        ...

    def ping(self) -> bool:
        """Return True when the backend is reachable and the key is valid."""
        ...

    def list_models(self) -> list[str]:
        """Return a list of available model names."""
        ...
