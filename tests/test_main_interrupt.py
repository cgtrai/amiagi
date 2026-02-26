from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import amiagi.main as app_main


class FakeMemoryRepository:
    def __init__(self, _db_path: Path) -> None:
        self._latest = None

    def clear_all(self) -> None:
        return None

    def latest_memory(self, kind: str, source: str | None = None):
        _ = (kind, source)
        return self._latest

    def replace_memory(self, kind: str, source: str, content: str) -> None:
        _ = (kind, source, content)


class CapturingMemoryRepository(FakeMemoryRepository):
    def __init__(self, _db_path: Path) -> None:
        super().__init__(_db_path)
        self.replaced: list[tuple[str, str, str]] = []

    def replace_memory(self, kind: str, source: str, content: str) -> None:
        self.replaced.append((kind, source, content))


class FakeActivityLogger:
    def __init__(self, _path: Path) -> None:
        self.actions: list[str] = []

    def log(self, action: str, intent: str, details: dict | None = None) -> None:
        _ = (intent, details)
        self.actions.append(action)


class FakeOllamaClient:
    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)
        self.base_url = "http://127.0.0.1:11434"

    def ping(self) -> bool:
        return True


class FakeChatService:
    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)


def test_main_reads_startup_dialogue_from_work_dir_when_default_relative_path_missing(
    tmp_path: Path, monkeypatch
) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "wprowadzenie.md").write_text("Start eksperymentu", encoding="utf-8")

    settings = SimpleNamespace(
        work_dir=work_dir,
        db_path=tmp_path / "amiagi.db",
        executor_model_io_log_path=tmp_path / "model_io_executor.jsonl",
        supervisor_model_io_log_path=tmp_path / "model_io_supervisor.jsonl",
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        model_io_log_path=tmp_path / "model_io.jsonl",
        activity_log_path=tmp_path / "activity.jsonl",
        shell_policy_path=tmp_path / "shell_allowlist.json",
        supervisor_min_free_vram_mb=3000,
        model_queue_max_wait_seconds=60,
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="test-model",
        ollama_request_timeout_seconds=120,
        ollama_max_retries=1,
        ollama_retry_backoff_seconds=1.0,
        supervisor_enabled=False,
        max_context_memories=20,
        supervisor_model="supervisor-model",
        supervisor_request_timeout_seconds=60,
        supervisor_max_repair_rounds=2,
        autonomous_mode=False,
    )

    repositories: list[CapturingMemoryRepository] = []

    def memory_factory(db_path: Path) -> CapturingMemoryRepository:
        repo = CapturingMemoryRepository(db_path)
        repositories.append(repo)
        return repo

    monkeypatch.setattr(app_main.Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(app_main, "MemoryRepository", memory_factory)
    monkeypatch.setattr(app_main, "ModelIOLogger", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_main, "ActivityLogger", lambda _path: FakeActivityLogger(settings.activity_log_path))
    monkeypatch.setattr(app_main, "ModelQueuePolicy", lambda **kwargs: object())
    monkeypatch.setattr(app_main, "VramAdvisor", lambda: object())
    monkeypatch.setattr(app_main, "OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(app_main, "ChatService", FakeChatService)
    monkeypatch.setattr(app_main, "run_cli", lambda *args, **kwargs: None)

    app_main.main([])

    assert repositories
    replaced_kinds = [kind for kind, _source, _content in repositories[0].replaced]
    assert "discussion_context" in replaced_kinds
    assert "session_summary" in replaced_kinds


def test_main_handles_keyboard_interrupt_without_traceback(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    settings = SimpleNamespace(
        work_dir=work_dir,
        db_path=tmp_path / "amiagi.db",
        executor_model_io_log_path=tmp_path / "model_io_executor.jsonl",
        supervisor_model_io_log_path=tmp_path / "model_io_supervisor.jsonl",
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        model_io_log_path=tmp_path / "model_io.jsonl",
        activity_log_path=tmp_path / "activity.jsonl",
        shell_policy_path=tmp_path / "shell_allowlist.json",
        supervisor_min_free_vram_mb=3000,
        model_queue_max_wait_seconds=60,
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="test-model",
        ollama_request_timeout_seconds=120,
        ollama_max_retries=1,
        ollama_retry_backoff_seconds=1.0,
        supervisor_enabled=False,
        max_context_memories=20,
        supervisor_model="supervisor-model",
        supervisor_request_timeout_seconds=60,
        supervisor_max_repair_rounds=2,
        autonomous_mode=False,
    )

    fake_activity = FakeActivityLogger(settings.activity_log_path)

    monkeypatch.setattr(app_main.Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(app_main, "MemoryRepository", FakeMemoryRepository)
    monkeypatch.setattr(app_main, "ModelIOLogger", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_main, "ActivityLogger", lambda _path: fake_activity)
    monkeypatch.setattr(app_main, "ModelQueuePolicy", lambda **kwargs: object())
    monkeypatch.setattr(app_main, "VramAdvisor", lambda: object())
    monkeypatch.setattr(app_main, "OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(app_main, "ChatService", FakeChatService)
    monkeypatch.setattr(
        app_main,
        "run_cli",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    app_main.main(["--startup_dialogue_path", str(tmp_path / "missing.md")])

    output = capsys.readouterr().out
    assert "Zamknięto sesję (Ctrl+C)." in output
    assert "Traceback" not in output
    assert "session.interrupt" in fake_activity.actions


def test_main_auto_flag_forces_autonomous_mode(tmp_path: Path, monkeypatch) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    settings = SimpleNamespace(
        work_dir=work_dir,
        db_path=tmp_path / "amiagi.db",
        executor_model_io_log_path=tmp_path / "model_io_executor.jsonl",
        supervisor_model_io_log_path=tmp_path / "model_io_supervisor.jsonl",
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        model_io_log_path=tmp_path / "model_io.jsonl",
        activity_log_path=tmp_path / "activity.jsonl",
        shell_policy_path=tmp_path / "shell_allowlist.json",
        supervisor_min_free_vram_mb=3000,
        model_queue_max_wait_seconds=60,
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="test-model",
        ollama_request_timeout_seconds=120,
        ollama_max_retries=1,
        ollama_retry_backoff_seconds=1.0,
        supervisor_enabled=False,
        max_context_memories=20,
        supervisor_model="supervisor-model",
        supervisor_request_timeout_seconds=60,
        supervisor_max_repair_rounds=2,
        autonomous_mode=False,
    )

    run_cli_kwargs: dict = {}

    def fake_run_cli(*_args, **kwargs):
        run_cli_kwargs.update(kwargs)

    monkeypatch.setattr(app_main.Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(app_main, "MemoryRepository", FakeMemoryRepository)
    monkeypatch.setattr(app_main, "ModelIOLogger", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_main, "ActivityLogger", lambda _path: FakeActivityLogger(settings.activity_log_path))
    monkeypatch.setattr(app_main, "ModelQueuePolicy", lambda **kwargs: object())
    monkeypatch.setattr(app_main, "VramAdvisor", lambda: object())
    monkeypatch.setattr(app_main, "OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(app_main, "ChatService", FakeChatService)
    monkeypatch.setattr(app_main, "run_cli", fake_run_cli)

    app_main.main(["-auto", "--startup_dialogue_path", str(tmp_path / "missing.md")])

    assert run_cli_kwargs.get("autonomous_mode") is True


def test_main_auto_flag_works_with_frozen_settings_dataclass(tmp_path: Path, monkeypatch) -> None:
    settings = app_main.Settings(
        work_dir=tmp_path / "amiagi-my-work",
        db_path=tmp_path / "amiagi.db",
        executor_model_io_log_path=tmp_path / "model_io_executor.jsonl",
        supervisor_model_io_log_path=tmp_path / "model_io_supervisor.jsonl",
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        model_io_log_path=tmp_path / "model_io.jsonl",
        activity_log_path=tmp_path / "activity.jsonl",
        shell_policy_path=tmp_path / "shell_allowlist.json",
        supervisor_enabled=False,
        autonomous_mode=False,
    )

    run_cli_kwargs: dict = {}

    def fake_run_cli(*_args, **kwargs):
        run_cli_kwargs.update(kwargs)

    monkeypatch.setattr(app_main.Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(app_main, "MemoryRepository", FakeMemoryRepository)
    monkeypatch.setattr(app_main, "ModelIOLogger", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_main, "ActivityLogger", lambda _path: FakeActivityLogger(settings.activity_log_path))
    monkeypatch.setattr(app_main, "ModelQueuePolicy", lambda **kwargs: object())
    monkeypatch.setattr(app_main, "VramAdvisor", lambda: object())
    monkeypatch.setattr(app_main, "OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(app_main, "ChatService", FakeChatService)
    monkeypatch.setattr(app_main, "run_cli", fake_run_cli)

    app_main.main(["-cs", "-auto", "--startup_dialogue_path", str(tmp_path / "missing.md")])

    assert run_cli_kwargs.get("autonomous_mode") is True


def test_main_vram_off_disables_runtime_queue_policy(tmp_path: Path, monkeypatch, capsys) -> None:
    settings = app_main.Settings(
        work_dir=tmp_path / "amiagi-my-work",
        db_path=tmp_path / "amiagi.db",
        executor_model_io_log_path=tmp_path / "model_io_executor.jsonl",
        supervisor_model_io_log_path=tmp_path / "model_io_supervisor.jsonl",
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        model_io_log_path=tmp_path / "model_io.jsonl",
        activity_log_path=tmp_path / "activity.jsonl",
        shell_policy_path=tmp_path / "shell_allowlist.json",
        supervisor_enabled=False,
        autonomous_mode=False,
    )

    run_cli_kwargs: dict = {}

    def fail_if_queue_policy_created(**_kwargs):
        raise AssertionError("ModelQueuePolicy should not be created when -vram-off is enabled")

    def fake_run_cli(*_args, **kwargs):
        run_cli_kwargs.update(kwargs)

    monkeypatch.setattr(app_main.Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(app_main, "MemoryRepository", FakeMemoryRepository)
    monkeypatch.setattr(app_main, "ModelIOLogger", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_main, "ActivityLogger", lambda _path: FakeActivityLogger(settings.activity_log_path))
    monkeypatch.setattr(app_main, "ModelQueuePolicy", fail_if_queue_policy_created)
    monkeypatch.setattr(app_main, "VramAdvisor", lambda: object())
    monkeypatch.setattr(app_main, "OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(app_main, "ChatService", FakeChatService)
    monkeypatch.setattr(app_main, "run_cli", fake_run_cli)

    app_main.main(["-vram-off", "--startup_dialogue_path", str(tmp_path / "missing.md")])

    output = capsys.readouterr().out
    assert "Kontrola VRAM runtime: OFF" in output
    assert "Polityka kolejki modeli: WYŁĄCZONA (-vram-off)" in output
    assert "shell_policy_path" in run_cli_kwargs


def test_main_cold_start_clears_supervision_dialogue_log(tmp_path: Path, monkeypatch) -> None:
    settings = app_main.Settings(
        work_dir=tmp_path / "amiagi-my-work",
        db_path=tmp_path / "amiagi.db",
        executor_model_io_log_path=tmp_path / "model_io_executor.jsonl",
        supervisor_model_io_log_path=tmp_path / "model_io_supervisor.jsonl",
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        model_io_log_path=tmp_path / "model_io.jsonl",
        activity_log_path=tmp_path / "activity.jsonl",
        shell_policy_path=tmp_path / "shell_allowlist.json",
        supervisor_enabled=False,
        autonomous_mode=False,
    )

    settings.executor_model_io_log_path.parent.mkdir(parents=True, exist_ok=True)
    settings.executor_model_io_log_path.write_text("executor-old", encoding="utf-8")
    settings.supervisor_model_io_log_path.write_text("supervisor-old", encoding="utf-8")
    settings.model_io_log_path.write_text("combined-old", encoding="utf-8")
    settings.supervisor_dialogue_log_path.write_text("dialogue-old", encoding="utf-8")

    monkeypatch.setattr(app_main.Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(app_main, "MemoryRepository", FakeMemoryRepository)
    monkeypatch.setattr(app_main, "ModelIOLogger", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_main, "ActivityLogger", lambda _path: FakeActivityLogger(settings.activity_log_path))
    monkeypatch.setattr(app_main, "ModelQueuePolicy", lambda **kwargs: object())
    monkeypatch.setattr(app_main, "VramAdvisor", lambda: object())
    monkeypatch.setattr(app_main, "OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(app_main, "ChatService", FakeChatService)
    monkeypatch.setattr(app_main, "run_cli", lambda *_args, **_kwargs: None)

    app_main.main(["-cs", "--startup_dialogue_path", str(tmp_path / "missing.md")])

    assert settings.supervisor_dialogue_log_path.read_text(encoding="utf-8") == ""


def test_main_ui_textual_dispatches_to_textual_runner(tmp_path: Path, monkeypatch) -> None:
    settings = app_main.Settings(
        work_dir=tmp_path / "amiagi-my-work",
        db_path=tmp_path / "amiagi.db",
        executor_model_io_log_path=tmp_path / "model_io_executor.jsonl",
        supervisor_model_io_log_path=tmp_path / "model_io_supervisor.jsonl",
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        model_io_log_path=tmp_path / "model_io.jsonl",
        activity_log_path=tmp_path / "activity.jsonl",
        shell_policy_path=tmp_path / "shell_allowlist.json",
        supervisor_enabled=False,
        autonomous_mode=False,
    )
    textual_kwargs: dict = {}

    monkeypatch.setattr(app_main.Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(app_main, "MemoryRepository", FakeMemoryRepository)
    monkeypatch.setattr(app_main, "ModelIOLogger", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_main, "ActivityLogger", lambda _path: FakeActivityLogger(settings.activity_log_path))
    monkeypatch.setattr(app_main, "ModelQueuePolicy", lambda **kwargs: object())
    monkeypatch.setattr(app_main, "VramAdvisor", lambda: object())
    monkeypatch.setattr(app_main, "OllamaClient", FakeOllamaClient)
    monkeypatch.setattr(app_main, "ChatService", FakeChatService)
    def fail_run_cli(*_args, **_kwargs):
        raise AssertionError("run_cli should not be called in textual mode")

    monkeypatch.setattr(app_main, "run_cli", fail_run_cli)
    monkeypatch.setattr(
        app_main,
        "run_textual_cli",
        lambda **kwargs: textual_kwargs.update(kwargs),
    )

    app_main.main(["--ui", "textual", "--startup_dialogue_path", str(tmp_path / "missing.md")])

    assert textual_kwargs.get("supervisor_dialogue_log_path") == settings.supervisor_dialogue_log_path
