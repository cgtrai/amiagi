from __future__ import annotations

from pathlib import Path

from amiagi.application.chat_service import ChatService
from amiagi.infrastructure.memory_repository import MemoryRepository


class FakeOllamaClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.next_response = "test-response"

    def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str,
        num_ctx: int | None = None,
    ) -> str:
        self.calls.append(
            {"messages": messages, "system_prompt": system_prompt, "num_ctx": num_ctx}
        )
        return self.next_response


def test_chat_service_stores_interaction(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    client = FakeOllamaClient()
    service = ChatService(memory_repository=repository, ollama_client=client)

    answer = service.ask("jak działa sqlite?")

    assert answer == "test-response"
    history = repository.recent_messages(limit=10)
    assert [message.role for message in history] == ["user", "assistant"]
    assert "jak działa sqlite?" in history[0].content


def test_remember_adds_note_memory(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    client = FakeOllamaClient()
    service = ChatService(memory_repository=repository, ollama_client=client)

    service.remember("zapamiętaj stack: python + ollama")
    memories = repository.search_memories(query="ollama", limit=10)

    assert len(memories) == 1
    assert memories[0].kind == "note"


def test_generate_python_code_strips_markdown_fences(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    client = FakeOllamaClient()
    client.next_response = """```python
print('hello')
```"""
    service = ChatService(memory_repository=repository, ollama_client=client)

    code = service.generate_python_code("wypisz hello")

    assert code == "print('hello')"
    generated = repository.search_memories(query="wypisz hello", limit=10)
    assert generated


def test_summarize_session_for_restart_saves_memory(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    repository.append_message("user", "cel: zbudować narzędzie")
    repository.append_message("assistant", "zrobione etapy A i B")

    client = FakeOllamaClient()
    client.next_response = "- cel\n- etapy\n- następne kroki"
    service = ChatService(memory_repository=repository, ollama_client=client)

    summary = service.summarize_session_for_restart()

    assert "następne kroki" in summary
    latest = repository.latest_memory(kind="session_summary", source="session_end")
    assert latest is not None
    assert "cel" in latest.content


def test_ask_includes_framework_protocol_and_startup_context(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    repository.replace_memory(
        kind="discussion_context",
        source="imported_dialogue",
        content="poprzednia dyskusja: eksperyment z frameworkiem",
    )
    repository.replace_memory(
        kind="session_summary",
        source="startup_seed",
        content="podsumowanie: mamy gotowy framework i zasady zgód",
    )
    client = FakeOllamaClient()
    service = ChatService(memory_repository=repository, ollama_client=client)

    _ = service.ask("co dalej?")

    assert client.calls
    system_prompt = client.calls[-1]["system_prompt"]
    assert "PROTOKÓŁ FRAMEWORKA" in system_prompt
    assert "poprzednia dyskusja" in system_prompt
    assert "podsumowanie" in system_prompt


def test_build_system_prompt_contains_runtime_guide(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    client = FakeOllamaClient()
    service = ChatService(memory_repository=repository, ollama_client=client)

    prompt = service.build_system_prompt("test")

    assert "PROTOKÓŁ FRAMEWORKA" in prompt
    assert "Kontekst z pamięci" in prompt or "Brak zapisanych wspomnień" in prompt


def test_build_system_prompt_uses_autonomous_executor_role(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    client = FakeOllamaClient()
    service = ChatService(memory_repository=repository, ollama_client=client)

    prompt = service.build_system_prompt("kontynuuj")

    assert "autonomicznym modelem wykonawczym" in prompt
    assert "lokalnym asystentem programistycznym" not in prompt


def test_build_system_prompt_contains_work_dir_protocol(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    client = FakeOllamaClient()
    work_dir = tmp_path / "amiagi-my-work"
    service = ChatService(memory_repository=repository, ollama_client=client, work_dir=work_dir)

    prompt = service.build_system_prompt("utwórz narzędzie")

    assert "KATALOG ROBOCZY MODELU" in prompt
    assert str(work_dir.resolve()) in prompt
    assert "write_file/append_file -> run_python" in prompt


def test_build_system_prompt_includes_persisted_main_plan_context(tmp_path: Path) -> None:
        repository = MemoryRepository(tmp_path / "chat.db")
        client = FakeOllamaClient()
        work_dir = tmp_path / "amiagi-my-work"
        notes_dir = work_dir / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "main_plan.json").write_text(
                """
{
    "goal": "Zebrać informacje prawne o AI",
    "key_achievement": "Uruchomiono badanie",
    "current_stage": "research_start",
    "tasks": [
        {"id": "T1", "title": "Skompletować źródła", "status": "rozpoczęta", "next_step": "Wyszukać akty"}
    ]
}
""".strip(),
                encoding="utf-8",
        )

        service = ChatService(memory_repository=repository, ollama_client=client, work_dir=work_dir)
        prompt = service.build_system_prompt("kontynuuj")

        assert "Kontekst planu głównego (trwały)" in prompt
        assert "Zebrać informacje prawne o AI" in prompt
        assert "tasks_done: 0/1" in prompt


def test_bootstrap_runtime_readiness_saves_status(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    repository.replace_memory(
        kind="session_summary",
        source="startup_seed",
        content="podsumowanie startowe",
    )
    client = FakeOllamaClient()
    client.next_response = "Jestem gotowy do współpracy."
    service = ChatService(memory_repository=repository, ollama_client=client)

    readiness = service.bootstrap_runtime_readiness()

    assert "gotowy" in readiness.lower()
    latest = repository.latest_memory(kind="runtime_readiness", source="startup_bootstrap")
    assert latest is not None
    assert "gotowy" in latest.content.lower()


def test_framework_meta_query_is_answered_without_model_call(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    client = FakeOllamaClient()
    service = ChatService(memory_repository=repository, ollama_client=client)

    answer = service.ask("czy wiesz jak działa framework za pomocą którego się komunikujemy?")

    assert "framework amiagi" in answer.lower()
    assert len(client.calls) == 0


def test_framework_meta_query_mentions_work_dir_and_tool_calling(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    client = FakeOllamaClient()
    work_dir = tmp_path / "amiagi-my-work"
    service = ChatService(memory_repository=repository, ollama_client=client, work_dir=work_dir)

    answer = service.ask("jak używać framework?")

    assert str(work_dir.resolve()) in answer
    assert "tool_call" in answer
    assert len(client.calls) == 0


def test_autonomy_trigger_includes_existing_intro_path(tmp_path: Path, monkeypatch) -> None:
    repository = MemoryRepository(tmp_path / "chat.db")
    client = FakeOllamaClient()
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    intro = tmp_path / "wprowadzenie.md"
    intro.write_text("start", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    service = ChatService(memory_repository=repository, ollama_client=client, work_dir=work_dir)
    augmented = service._augment_user_message("działaj")

    assert str(intro.resolve()) in augmented
