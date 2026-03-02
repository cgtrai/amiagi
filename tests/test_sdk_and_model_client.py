"""Tests for SDK import alias and ChatService model_client rename."""

from __future__ import annotations

from pathlib import Path

from amiagi.application.chat_service import ChatService
from amiagi.infrastructure.memory_repository import MemoryRepository


class FakeClient:
    _is_api_client = False
    model: str = "fake-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.next_response = "response"

    def chat(self, messages: list[dict[str, str]], system_prompt: str = "", num_ctx: int | None = None) -> str:
        self.calls.append({"messages": messages})
        return self.next_response

    def ping(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return [self.model]


# ---- SDK import ----

def test_sdk_import() -> None:
    """Verify ``from amiagi.sdk import AmiagiClient`` works."""
    from amiagi.sdk import AmiagiClient
    assert AmiagiClient is not None


# ---- model_client property on ChatService ----

def test_model_client_getter(tmp_path: Path) -> None:
    client = FakeClient()
    repo = MemoryRepository(tmp_path / "mem.db")
    svc = ChatService(memory_repository=repo, model_client=client)
    assert svc.model_client is client


def test_model_client_setter(tmp_path: Path) -> None:
    client1 = FakeClient()
    client2 = FakeClient()
    repo = MemoryRepository(tmp_path / "mem.db")
    svc = ChatService(memory_repository=repo, model_client=client1)
    svc.model_client = client2
    assert svc.model_client is client2
    assert svc.ollama_client is client2  # backward compat field


def test_is_api_model_uses_model_client(tmp_path: Path) -> None:
    client = FakeClient()
    client._is_api_client = True
    repo = MemoryRepository(tmp_path / "mem.db")
    svc = ChatService(memory_repository=repo, model_client=client)
    assert svc.is_api_model() is True


def test_chat_uses_model_client_internally(tmp_path: Path) -> None:
    """Verify internal calls go through model_client (no AttributeError)."""
    client = FakeClient()
    repo = MemoryRepository(tmp_path / "mem.db")
    svc = ChatService(memory_repository=repo, model_client=client)
    answer = svc.ask("hello")
    assert answer == "response"
    assert len(client.calls) == 1


# ---- SupervisorService model_client property ----

def test_supervisor_model_client_property() -> None:
    from amiagi.application.supervisor_service import SupervisorService

    client = FakeClient()
    svc = SupervisorService(model_client=client)
    assert svc.model_client is client

    client2 = FakeClient()
    svc.model_client = client2
    assert svc.ollama_client is client2
