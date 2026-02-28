from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from amiagi.interfaces.textual_cli import (
    INTERRUPT_AUTORESUME_IDLE_SECONDS,
    _AmiagiTextualApp,
    _CommandOutcome,
    _canonical_tool_name,
    _copy_to_system_clipboard,
    _handle_textual_command,
    _is_model_access_allowed,
)
from amiagi.application.supervisor_service import SupervisionResult


class DummyPermissionManager:
    def __init__(self) -> None:
        self.allow_all = False
        self.granted_once: set[str] = set()


def test_textual_help_command_returns_help_text() -> None:
    permission_manager = DummyPermissionManager()

    outcome = _handle_textual_command("/help", permission_manager)

    assert outcome.handled is True
    assert outcome.should_exit is False
    assert outcome.messages
    merged = "\n".join(outcome.messages)
    assert "Komendy (textual):" in merged
    assert "/permissions" in merged
    assert "/run-shell" in merged
    assert "/router-status" in merged
    # CLI-only header should NOT appear in Textual help
    assert merged.startswith("Komendy (textual):")


def test_textual_permissions_all_and_status_commands() -> None:
    permission_manager = DummyPermissionManager()

    outcome_all = _handle_textual_command("/permissions all", permission_manager)
    outcome_status = _handle_textual_command("/permissions", permission_manager)

    assert outcome_all.handled is True
    assert permission_manager.allow_all is True
    assert "Włączono globalną zgodę na zasoby." in outcome_all.messages
    assert outcome_status.handled is True
    assert "allow_all: True" in outcome_status.messages


def test_textual_permissions_reset_clears_grants() -> None:
    permission_manager = DummyPermissionManager()
    permission_manager.granted_once.add("network.local")

    outcome = _handle_textual_command("/permissions reset", permission_manager)

    assert outcome.handled is True
    assert permission_manager.granted_once == set()
    assert "Wyczyszczono zapamiętane zgody per zasób." in outcome.messages


def test_textual_permissions_ask_disables_global_mode() -> None:
    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True

    outcome = _handle_textual_command("/permissions ask", permission_manager)

    assert outcome.handled is True
    assert permission_manager.allow_all is False
    assert "Włączono tryb pytań o zgodę per zasób." in outcome.messages[0]


def test_textual_router_status_command_returns_actor_states(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "ok"

    permission_manager = DummyPermissionManager()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService()),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    outcome = app._handle_cli_like_commands("/router-status")

    assert outcome.handled is True
    merged = "\n".join(outcome.messages)
    assert "ROUTER STATUS" in merged
    assert "Router:" in merged
    assert "Polluks:" in merged
    assert "Kastor:" in merged
    assert "Terminal:" in merged


def test_textual_idle_until_command_sets_and_clears_schedule(tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "ok"

    permission_manager = DummyPermissionManager()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService()),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    set_outcome = app._handle_cli_like_commands("/idle-until 2030-01-01T00:00:00Z")
    clear_outcome = app._handle_cli_like_commands("/idle-until off")

    assert set_outcome.handled is True
    assert any("Ustawiono IDLE until" in item for item in set_outcome.messages)
    assert clear_outcome.handled is True
    assert "Wyczyszczono zaplanowane okno IDLE." in clear_outcome.messages


def test_textual_models_show_and_chose_commands(tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

        def __init__(self) -> None:
            self.model = "legacy-model"

        def list_models(self) -> list[str]:
            return ["model-a", "model-b"]

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "ok"

    permission_manager = DummyPermissionManager()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService()),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    show_outcome = app._handle_cli_like_commands("/models show")
    choose_outcome = app._handle_cli_like_commands("/models chose 2")

    assert show_outcome.handled is True
    show_merged = "\n".join(show_outcome.messages)
    assert "MODELE OLLAMA" in show_merged
    assert "1. model-a" in show_merged
    assert "2. model-b" in show_merged

    assert choose_outcome.handled is True
    assert "Aktywny model wykonawczy: model-b" in "\n".join(choose_outcome.messages)
    assert app._chat_service.ollama_client.model == "model-b"


def test_textual_models_current_command_returns_active_model(tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

        def __init__(self) -> None:
            self.model = "model-current-test"

        def list_models(self) -> list[str]:
            return ["model-current-test"]

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "ok"

    permission_manager = DummyPermissionManager()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService()),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    outcome = app._handle_cli_like_commands("/models current")

    assert outcome.handled is True
    assert "Aktywny model wykonawczy: model-current-test" in "\n".join(outcome.messages)


def test_textual_cls_command_clears_main_panel(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "ok"

    permission_manager = DummyPermissionManager()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService()),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    captured: list[bool] = []
    monkeypatch.setattr(app, "_clear_textual_panels", lambda *, clear_all: captured.append(clear_all))
    monkeypatch.setattr(app, "notify", lambda _msg, severity="information": None)

    outcome = app._handle_cli_like_commands("/cls")

    assert outcome.handled is True
    assert outcome.messages == []
    assert captured == [False]


def test_textual_cls_all_command_clears_all_panels(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "ok"

    permission_manager = DummyPermissionManager()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService()),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    captured: list[bool] = []
    monkeypatch.setattr(app, "_clear_textual_panels", lambda *, clear_all: captured.append(clear_all))
    monkeypatch.setattr(app, "notify", lambda _msg, severity="information": None)

    outcome = app._handle_cli_like_commands("/cls all")

    assert outcome.handled is True
    assert outcome.messages == []
    assert captured == [True]


def test_textual_model_access_allows_when_global_mode_enabled() -> None:
    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True

    allowed, network_resource = _is_model_access_allowed(permission_manager, "http://127.0.0.1:11434")

    assert allowed is True
    assert network_resource == "network.local"


def test_textual_model_access_allows_when_resource_granted_once() -> None:
    permission_manager = DummyPermissionManager()
    permission_manager.granted_once.add("network.internet")

    allowed, network_resource = _is_model_access_allowed(permission_manager, "https://example.org")

    assert allowed is True
    assert network_resource == "network.internet"


def test_textual_model_access_denies_without_permission() -> None:
    permission_manager = DummyPermissionManager()

    allowed, network_resource = _is_model_access_allowed(permission_manager, "http://localhost:11434")

    assert allowed is False
    assert network_resource == "network.local"


def test_textual_bindings_include_copy_and_safe_quit() -> None:
    keys: set[str] = set()
    for binding in _AmiagiTextualApp.BINDINGS:
        if isinstance(binding, tuple):
            keys.add(binding[0])
        else:
            keys.add(binding.key)

    assert "ctrl+c" in keys
    assert "ctrl+shift+c" in keys
    assert "ctrl+q" in keys


def test_copy_to_system_clipboard_returns_error_without_gui_env(monkeypatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)

    copied, details = _copy_to_system_clipboard("abc")

    assert copied is False
    assert "WAYLAND_DISPLAY/DISPLAY" in details


def test_copy_to_system_clipboard_uses_xclip_when_available(monkeypatch) -> None:
    class _Completed:
        returncode = 0

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("amiagi.interfaces.textual_cli.shutil.which", lambda name: "/usr/bin/xclip" if name == "xclip" else None)
    monkeypatch.setattr("amiagi.interfaces.textual_cli.subprocess.run", lambda *args, **kwargs: _Completed())

    copied, details = _copy_to_system_clipboard("abc")

    assert copied is True
    assert "xclip" in details


def test_copy_to_system_clipboard_times_out_instead_of_blocking(monkeypatch) -> None:
    def _blocking_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="xclip", timeout=0.35)

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("amiagi.interfaces.textual_cli.shutil.which", lambda name: "/usr/bin/xclip" if name == "xclip" else None)
    monkeypatch.setattr("amiagi.interfaces.textual_cli.subprocess.run", _blocking_run)

    copied, details = _copy_to_system_clipboard("abc")

    assert copied is False
    assert "Przekroczono limit czasu" in details


def test_textual_user_turn_uses_supervisor_refine(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            self.calls.append(
                {
                    "user_message": user_message,
                    "model_answer": model_answer,
                    "stage": stage,
                }
            )
            return SupervisionResult(
                answer="```tool_call\n{\"tool\":\"list_dir\",\"args\":{\"path\":\".\"},\"intent\":\"test\"}\n```",
                repairs_applied=1,
                status="repair",
                reason_code="NO_TOOL_CALL",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            assert "kontynuuj" in text
            return "Pewnie, mogę pomóc opisowo."

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    chat_service = _DummyChatService(supervisor)
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}

    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))
    monkeypatch.setattr(app, "_enforce_supervised_progress", lambda _text, answer, **_kwargs: answer)
    monkeypatch.setattr(app, "_resolve_tool_calls", lambda answer: answer)

    event = SimpleNamespace(value="kontynuuj", input=SimpleNamespace(value="kontynuuj"))

    app.on_input_submitted(cast(Any, event))

    assert event.input.value == ""
    assert len(supervisor.calls) == 1
    assert supervisor.calls[0]["stage"] == "user_turn"
    assert supervisor.calls[0]["model_answer"] == "Pewnie, mogę pomóc opisowo."
    assert "[RUNTIME_SUPERVISION_CONTEXT]" in supervisor.calls[0]["user_message"]
    assert supervisor.calls[0]["user_message"].endswith("\nkontynuuj")
    assert logs["user_model_log"][-1].startswith("Model: Wykonałem krok operacyjny narzędziem 'list_dir'")
    assert "tool_call" not in logs["user_model_log"][-1]
    assert logs["executor_log"][-1].startswith("[user_turn] ```tool_call")


def test_textual_supervisor_forces_activity_after_passive_streak(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            self.calls.append(
                {
                    "user_message": user_message,
                    "model_answer": model_answer,
                    "stage": stage,
                }
            )
            if stage == "textual_no_action_corrective":
                return SupervisionResult(
                    answer="```tool_call\n{\"tool\":\"check_capabilities\",\"args\":{\"check_network\":false},\"intent\":\"aktywny_krok\"}\n```",
                    repairs_applied=1,
                    status="repair",
                    reason_code="NO_TOOL_CALL",
                )
            return SupervisionResult(
                answer="Pasywna odpowiedź bez działania.",
                repairs_applied=0,
                status="ok",
                reason_code="OK",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "Pasywna odpowiedź bez działania."

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    chat_service = _DummyChatService(supervisor)
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}

    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))
    monkeypatch.setattr(app, "_enforce_supervised_progress", lambda _text, answer, **_kwargs: answer)
    monkeypatch.setattr(app, "_resolve_tool_calls", lambda answer: answer)

    first_event = SimpleNamespace(value="kontynuuj", input=SimpleNamespace(value="x"))
    second_event = SimpleNamespace(value="kontynuuj", input=SimpleNamespace(value="y"))

    app.on_input_submitted(cast(Any, first_event))
    app.on_input_submitted(cast(Any, second_event))

    stages = [call["stage"] for call in supervisor.calls]
    assert stages == ["user_turn", "user_turn", "textual_no_action_corrective"]
    assert logs["user_model_log"][-1].startswith("Model: Wykonałem krok operacyjny narzędziem 'check_capabilities'")
    assert "tool_call" not in logs["user_model_log"][-1]
    assert logs["executor_log"][-1].startswith("[user_turn] ```tool_call")


def test_textual_executes_tool_call_and_uses_tool_result_followup(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()
            self.ask_payloads: list[str] = []

        def ask(self, text: str, *, actor: str = "") -> str:
            self.ask_payloads.append(text)
            if "kontynuuj" in text and not text.startswith("[TOOL_RESULT]"):
                return "```tool_call\n{\"tool\":\"list_dir\",\"args\":{\"path\":\".\"},\"intent\":\"scan\"}\n```"
            if text.startswith("[TOOL_RESULT]"):
                return "Wykonano krok i mogę kontynuować."
            return "Brak odpowiedzi"

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    chat_service = _DummyChatService()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))
    monkeypatch.setattr(app, "_enforce_supervised_progress", lambda _text, answer, **_kwargs: answer)

    event = SimpleNamespace(value="kontynuuj", input=SimpleNamespace(value="x"))
    app.on_input_submitted(cast(Any, event))

    assert len(chat_service.ask_payloads) == 2
    assert "kontynuuj" in chat_service.ask_payloads[0]
    assert chat_service.ask_payloads[1].startswith("[TOOL_RESULT]")
    assert logs["user_model_log"][-1] == "Model: Wykonano krok i mogę kontynuować."


def test_textual_unknown_tool_is_corrected_by_supervisor(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.stages: list[str] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            _ = (user_message, model_answer)
            self.stages.append(stage)
            if stage == "textual_unknown_tool_corrective":
                return SupervisionResult(
                    answer="```tool_call\n{\"tool\":\"list_dir\",\"args\":{\"path\":\".\"},\"intent\":\"recover\"}\n```",
                    repairs_applied=1,
                    status="repair",
                    reason_code="NO_TOOL_CALL",
                )
            return SupervisionResult(
                answer=model_answer,
                repairs_applied=0,
                status="ok",
                reason_code="OK",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()
            self.ask_payloads: list[str] = []

        def ask(self, text: str, *, actor: str = "") -> str:
            self.ask_payloads.append(text)
            if text == "kontynuuj":
                return "```tool_call\n{\"tool\":\"dialogue\",\"args\":{\"x\":1},\"intent\":\"bad\"}\n```"
            if text.startswith("[TOOL_RESULT]"):
                return "Naprawiono i wykonano krok."
            return "Brak"

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    chat_service = _DummyChatService(supervisor)
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))

    event = SimpleNamespace(value="kontynuuj", input=SimpleNamespace(value="x"))
    app.on_input_submitted(cast(Any, event))

    assert (
        "textual_unknown_tool_corrective" in supervisor.stages
        or "textual_progress_guard" in supervisor.stages
    )
    assert any(payload.startswith("[TOOL_RESULT]") for payload in chat_service.ask_payloads)
    assert logs["user_model_log"][-1] == "Model: Naprawiono i wykonano krok."


def test_textual_progress_guard_enforces_plan_or_action(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            self.calls.append({"user_message": user_message, "model_answer": model_answer, "stage": stage})
            if stage == "textual_progress_guard":
                return SupervisionResult(
                    answer="```tool_call\n{\"tool\":\"write_file\",\"args\":{\"path\":\"notes/main_plan.json\",\"content\":\"{\\\"goal\\\":\\\"g\\\",\\\"key_achievement\\\":\\\"k\\\",\\\"current_stage\\\":\\\"rozpoczęta\\\",\\\"tasks\\\":[{\\\"id\\\":\\\"T1\\\",\\\"title\\\":\\\"t\\\",\\\"status\\\":\\\"rozpoczęta\\\",\\\"next_step\\\":\\\"n\\\"}]}\",\"overwrite\":true},\"intent\":\"init_plan\"}\n```",
                    repairs_applied=1,
                    status="repair",
                    reason_code="NO_TOOL_CALL",
                )
            return SupervisionResult(
                answer=model_answer,
                repairs_applied=0,
                status="ok",
                reason_code="OK",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            if text.startswith("[TOOL_RESULT]"):
                return "Plan zapisany i kontynuuję."
            return "Pasywna odpowiedź."

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    chat_service = _DummyChatService(supervisor)
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))

    event = SimpleNamespace(value="kontynuuj", input=SimpleNamespace(value="x"))
    app.on_input_submitted(cast(Any, event))

    stages = [call["stage"] for call in supervisor.calls]
    assert "textual_progress_guard" in stages
    assert logs["user_model_log"][-1] == "Model: Plan zapisany i kontynuuję."


def test_textual_progress_guard_sanitizes_unsupported_supervisor_tool(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            self.calls.append({"user_message": user_message, "model_answer": model_answer, "stage": stage})
            if stage == "textual_progress_guard":
                return SupervisionResult(
                    answer=(
                        "```tool_call\n"
                        "{\"tool\":\"text_generation\",\"args\":{\"prompt\":\"Hej\"},\"intent\":\"bad_tool\"}\n"
                        "```"
                    ),
                    repairs_applied=1,
                    status="repair",
                    reason_code="TOOL_PROTOCOL_DRIFT",
                )
            return SupervisionResult(
                answer=model_answer,
                repairs_applied=0,
                status="ok",
                reason_code="OK",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()
            self.ask_payloads: list[str] = []

        def ask(self, text: str, *, actor: str = "") -> str:
            self.ask_payloads.append(text)
            if text.startswith("[TOOL_RESULT]"):
                return "Plan zapisany przez fallback i kontynuuję."
            return "Pasywna odpowiedź."

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    chat_service = _DummyChatService(supervisor)
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))

    event = SimpleNamespace(value="cześć kim jesteś?", input=SimpleNamespace(value="x"))
    app.on_input_submitted(cast(Any, event))

    assert not any(payload.startswith("[TOOL_RESULT]") for payload in chat_service.ask_payloads)
    assert logs["user_model_log"][-1].startswith("Model: ")
    assert "Czy chcesz, żebym kontynuował plan" in logs["user_model_log"][-1]


def test_textual_interrupt_identity_reply_pauses_plan_and_requests_decision(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            _ = (user_message, model_answer, stage)
            return SupervisionResult(
                answer="Jestem Polluks i działam w runtime amiagi. Mogę kontynuować Twoje zadanie.",
                repairs_applied=0,
                status="ok",
                reason_code="OK",
            )

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = _DummySupervisor()
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "Jestem Polluks i działam w runtime amiagi. Mogę kontynuować Twoje zadanie."

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService()),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))

    event = SimpleNamespace(value="kim jesteś?", input=SimpleNamespace(value="x"))
    app.on_input_submitted(cast(Any, event))

    assert app._plan_pause_active is True
    assert app._pending_user_decision is True
    assert logs["user_model_log"][-1].startswith("Model: Jestem Polluks, modelem wykonawczym frameworka amiagi.")
    assert "Czy chcesz, żebym kontynuował plan" in logs["user_model_log"][-1]


def test_textual_watchdog_auto_resumes_paused_plan_after_idle_timeout(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.stages: list[str] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            _ = (user_message, model_answer)
            self.stages.append(stage)
            return SupervisionResult(
                answer="```tool_call\n{\"tool\":\"list_dir\",\"args\":{\"path\":\".\"},\"intent\":\"resume\"}\n```",
                repairs_applied=0,
                status="ok",
                reason_code="OK",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            if text.startswith("[TOOL_RESULT]"):
                return "Wznowiłem plan po pauzie."
            return "```tool_call\n{\"tool\":\"list_dir\",\"args\":{\"path\":\".\"},\"intent\":\"resume\"}\n```"

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService(supervisor)),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr("amiagi.interfaces.textual_cli.time.monotonic", lambda: 10_000.0)

    app._last_user_message = "kontynuuj"
    app._pending_user_decision = True
    app._set_plan_paused(paused=True, reason="user_interrupt", source="test")
    app._plan_pause_started_monotonic = 0.0

    app._run_supervisor_idle_watchdog()

    assert app._plan_pause_active is False
    assert app._pending_user_decision is False
    assert "textual_interrupt_autoresume" in supervisor.stages
    assert logs["user_model_log"][-1].startswith(
        "Model(auto): Wykonałem krok operacyjny narzędziem 'list_dir'"
    )


def test_textual_watchdog_nudges_supervisor_after_inactivity(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.stages: list[str] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            _ = (user_message, model_answer)
            self.stages.append(stage)
            if stage == "textual_watchdog_nudge":
                return SupervisionResult(
                    answer="```tool_call\n{\"tool\":\"list_dir\",\"args\":{\"path\":\".\"},\"intent\":\"watchdog_reactivate\"}\n```",
                    repairs_applied=1,
                    status="repair",
                    reason_code="NO_TOOL_CALL",
                )
            return SupervisionResult(
                answer=model_answer,
                repairs_applied=0,
                status="ok",
                reason_code="OK",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type("Repo", (), {"recent_messages": lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            if text.startswith("[TOOL_RESULT]"):
                return "Watchdog uruchomił kolejny krok."
            return "Pasywna odpowiedź."

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    chat_service = _DummyChatService(supervisor)
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr("amiagi.interfaces.textual_cli.time.monotonic", lambda: 10_000.0)

    app._last_user_message = "kontynuuj"
    app._last_model_answer = "Pasywna odpowiedź."
    app._passive_turns = 2
    app._last_progress_monotonic = 0.0

    app._run_supervisor_idle_watchdog()

    assert "textual_watchdog_nudge" in supervisor.stages
    assert logs["user_model_log"][-1] == "Model(auto): Watchdog uruchomił kolejny krok."
    assert logs["executor_log"][-1] == "[watchdog] Watchdog uruchomił kolejny krok."


def test_textual_watchdog_respects_idle_until_schedule(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.called = False

        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            _ = (user_message, model_answer, stage)
            self.called = True
            return SupervisionResult(
                answer="```tool_call\n{\"tool\":\"list_dir\",\"args\":{\"path\":\".\"},\"intent\":\"watchdog_reactivate\"}\n```",
                repairs_applied=1,
                status="repair",
                reason_code="NO_TOOL_CALL",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "Pasywna odpowiedź."

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService(supervisor)),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr("amiagi.interfaces.textual_cli.time.monotonic", lambda: 10_000.0)
    monkeypatch.setattr("amiagi.interfaces.textual_cli.time.time", lambda: 100.0)

    app._last_user_message = "kontynuuj"
    app._last_model_answer = "Pasywna odpowiedź."
    app._passive_turns = 2
    app._last_progress_monotonic = 0.0
    app._set_idle_until(200.0, source="test")

    app._run_supervisor_idle_watchdog()

    assert supervisor.called is False


def test_textual_identity_interrupt_does_not_autoresume_without_user_decision(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.stages: list[str] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            _ = (user_message, model_answer)
            self.stages.append(stage)
            return SupervisionResult(
                answer=model_answer,
                repairs_applied=0,
                status="ok",
                reason_code="OK",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "Jestem Polluks, modelem wykonawczym frameworka amiagi."

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService(supervisor)),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr("amiagi.interfaces.textual_cli.time.monotonic", lambda: 10_000.0)

    app._last_user_message = "kim jesteś?"
    app._pending_user_decision = True
    app._pending_decision_identity_query = True
    app._set_plan_paused(paused=True, reason="user_interrupt", source="test")
    app._plan_pause_started_monotonic = 0.0

    app._run_supervisor_idle_watchdog()

    assert app._plan_pause_active is True
    assert "textual_interrupt_autoresume" not in supervisor.stages
    assert not logs["user_model_log"]


def test_textual_poll_supervision_dialogue_renders_direction_labels(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            _ = text
            return "ok"

    log_path = tmp_path / "supervision_dialogue.jsonl"
    entry_exchange = {
        "stage": "user_turn",
        "type": "review_exchange",
        "executor_answer": "Odpowiedź Polluksa",
    }
    entry_result = {
        "stage": "user_turn",
        "type": "review_result",
        "supervisor_raw_output": json.dumps(
            {
                "status": "ok",
                "reason_code": "OK",
                "work_state": "RUNNING",
                "notes": "krótka uwaga",
            },
            ensure_ascii=False,
        ),
        "status": "ok",
        "reason_code": "OK",
    }
    log_path.write_text(
        json.dumps(entry_exchange, ensure_ascii=False) + "\n" + json.dumps(entry_result, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    permission_manager = DummyPermissionManager()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService()),
        supervisor_dialogue_log_path=log_path,
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))

    app._poll_supervision_dialogue()

    assert logs["executor_log"]
    assert "[POLLUKS→KASTOR | user_turn:review_exchange]" in logs["executor_log"][0]
    assert logs["supervisor_log"]
    assert "[KASTOR→ROUTER | user_turn:review_result]" in logs["supervisor_log"][0]


def test_textual_queues_message_when_router_busy(monkeypatch, tmp_path: Path) -> None:
    """When router is busy, user message should be queued and echoed immediately."""
    from amiagi.interfaces.textual_cli import _AmiagiTextualApp, _CommandOutcome

    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()
            self.ask_payloads: list[str] = []

        def ask(self, text: str, *, actor: str = "") -> str:
            self.ask_payloads.append(text)
            return "odpowiedź"

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    chat_service = _DummyChatService()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))
    monkeypatch.setattr(app, "_enforce_supervised_progress", lambda _text, answer, **_kwargs: answer)
    monkeypatch.setattr(app, "_resolve_tool_calls", lambda answer: answer)

    # Simulate router busy
    app._router_cycle_in_progress = True
    event = SimpleNamespace(value="czekam w kolejce", input=SimpleNamespace(value="x"))
    app.on_input_submitted(cast(Any, event))

    # Message should be echoed immediately and queued
    assert any("[Sponsor -> all] Użytkownik: czekam w kolejce" in msg for msg in logs["user_model_log"])
    assert any("Wiadomość zakolejkowana" in msg for msg in logs["user_model_log"])
    assert len(app._user_message_queue) == 1

    # After cycle finishes, _drain_user_queue processes it
    app._router_cycle_in_progress = False
    app._drain_user_queue()
    assert len(app._user_message_queue) == 0
    assert len(chat_service.ask_payloads) == 1


# ---------------------------------------------------------------------------
# Issue 1 tests: idle/interrupt
# ---------------------------------------------------------------------------


def test_interrupt_autoresume_timeout_is_180_seconds() -> None:
    """User requested auto-resume timeout increase from 120s to 180s."""
    assert INTERRUPT_AUTORESUME_IDLE_SECONDS == 180.0


def test_canonical_tool_name_maps_file_read_alias() -> None:
    """file_read should be canonicalized to read_file."""
    assert _canonical_tool_name("file_read") == "read_file"
    assert _canonical_tool_name("read") == "read_file"
    assert _canonical_tool_name("dir_list") == "list_dir"
    assert _canonical_tool_name("run_command") == "run_shell"
    assert _canonical_tool_name("read_file") == "read_file"
    assert _canonical_tool_name("write_file") == "write_file"


def test_model_response_awaits_user_detects_question(tmp_path: Path) -> None:
    """When model response ends with a question mark, plan should be detected as awaiting user."""

    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            return "odpowiedź"

    permission_manager = DummyPermissionManager()
    chat_service = _DummyChatService()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    # Ends with question mark → True
    assert app._model_response_awaits_user("Co chcesz, żebym zrobił?") is True
    assert app._model_response_awaits_user("Jakiego narzędzia potrzebujesz?") is True
    # Polish question markers
    assert app._model_response_awaits_user("Proszę o decyzję w tej sprawie.") is True
    assert app._model_response_awaits_user("Czekam na Twoją decyzję.") is True
    # Normal statement → False
    assert app._model_response_awaits_user("Wykonałem polecenie.") is False
    assert app._model_response_awaits_user("") is False
    # tool_call → always False (model is NOT waiting)
    assert app._model_response_awaits_user(
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"test"}\n```'
    ) is False


def test_model_awaits_user_pauses_plan_in_user_turn(monkeypatch, tmp_path: Path) -> None:
    """When model's answer asks the user a question, plan should pause and watchdog should suspend."""

    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            return "Co chcesz, żebym zrobił dalej?"

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    chat_service = _DummyChatService()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))
    monkeypatch.setattr(app, "_enforce_supervised_progress", lambda _text, answer, **_kwargs: answer)
    monkeypatch.setattr(app, "_resolve_tool_calls", lambda answer: answer)

    # Send a non-interrupt message → model replies with a question
    app._process_user_turn("zrób coś pożytecznego")

    assert app._plan_pause_active is True
    assert app._plan_pause_reason == "model_awaits_user"
    assert app._pending_user_decision is True
    assert app._watchdog_suspended_until_user_input is True


def test_new_user_message_unsuspends_watchdog(monkeypatch, tmp_path: Path) -> None:
    """A new user message should reset watchdog suspension."""

    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            return "Kontynuuję."

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    chat_service = _DummyChatService()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, chat_service),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))
    monkeypatch.setattr(app, "_enforce_supervised_progress", lambda _text, answer, **_kwargs: answer)
    monkeypatch.setattr(app, "_resolve_tool_calls", lambda answer: answer)

    # Simulate suspended watchdog
    app._watchdog_suspended_until_user_input = True
    app._watchdog_attempts = 5
    app._plan_pause_active = True
    app._pending_user_decision = False

    app._process_user_turn("kontynuuj pracę")

    # Watchdog should be unsuspended after new user input
    assert app._watchdog_suspended_until_user_input is False
    assert app._watchdog_attempts == 0


def test_supervisor_waiting_user_decision_pauses_plan(monkeypatch, tmp_path: Path) -> None:
    """When supervisor returns work_state=WAITING_USER_DECISION, plan should pause."""

    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            return SupervisionResult(
                answer="Oczekuję na decyzję użytkownika.",
                repairs_applied=0,
                status="ok",
                reason_code="OK",
                work_state="WAITING_USER_DECISION",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            return "Oczekuję na decyzję użytkownika."

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService(supervisor)),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))
    monkeypatch.setattr(app, "_poll_supervision_dialogue", lambda: None)
    monkeypatch.setattr(app, "_handle_cli_like_commands", lambda text: _CommandOutcome(False, []))
    monkeypatch.setattr(app, "_enforce_supervised_progress", lambda _text, answer, **_kwargs: answer)
    monkeypatch.setattr(app, "_resolve_tool_calls", lambda answer: answer)

    app._process_user_turn("zrób coś")

    assert app._plan_pause_active is True
    assert app._pending_user_decision is True
    assert app._watchdog_suspended_until_user_input is True


# ---------------------------------------------------------------------------
# Issue 2 tests: tool-creation workflow
# ---------------------------------------------------------------------------


def test_resolve_tool_calls_forces_tool_plan_after_max_corrections(monkeypatch, tmp_path: Path) -> None:
    """After 2 failed corrections for the same unknown tool, force write_file with tool plan."""

    correction_count = 0

    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def refine(self, *, user_message: str, model_answer: str, stage: str, conversation_excerpt: str = "") -> SupervisionResult:
            nonlocal correction_count
            correction_count += 1
            # Keep returning the same unknown tool — simulate stuck corrective loop
            return SupervisionResult(
                answer='```tool_call\n{"tool":"amiagi-execute","args":{"file_path":"test.py"},"intent":"run"}\n```',
                repairs_applied=1,
                status="repair",
                reason_code="TOOL_PROTOCOL_DRIFT",
                work_state="STALLED",
            )

    class _DummyChatService:
        def __init__(self, supervisor: _DummySupervisor) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = supervisor
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            if text.startswith("[TOOL_RESULT]"):
                return "Plan narzędzia zapisany."
            return "odpowiedź"

    permission_manager = DummyPermissionManager()
    permission_manager.allow_all = True
    supervisor = _DummySupervisor()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService(supervisor)),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))

    # Create work dir for write_file to work
    (tmp_path / "work" / "notes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "state").mkdir(parents=True, exist_ok=True)

    initial = '```tool_call\n{"tool":"amiagi-execute","args":{"file_path":"test.py"},"intent":"run"}\n```'
    result = app._resolve_tool_calls(initial)

    # After 2 corrective loops the supervisor keeps returning the unknown tool,
    # so on 3rd attempt the system should force a write_file with tool plan
    assert "tool_design_plan.json" in result or "force_tool_creation_plan" in result
    # The supervisor log should mention exhausted corrections
    assert any("Wyczerpano próby naprawy" in msg for msg in logs["supervisor_log"])


def test_enqueue_supervisor_message_routes_sponsor_blocks_to_user_panel(monkeypatch, tmp_path: Path) -> None:
    """When Kastor's notes contain [Kastor -> Sponsor], the block must appear in user_model_log."""

    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            return ""

    permission_manager = DummyPermissionManager()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService()),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))

    # Kastor sends a message addressed to Sponsor
    app._enqueue_supervisor_message(
        stage="user_turn",
        reason_code="IDENTITY_QUERY_HANDLED",
        notes="[Kastor -> Sponsor] Jestem Kastorem i współpracuję z Polluksem w realizacji Twoich zadań.",
        answer="",
    )

    # The [Kastor -> Sponsor] block must appear in user_model_log
    user_msgs = " ".join(logs["user_model_log"])
    assert "Kastor -> Sponsor" in user_msgs
    assert "Jestem Kastorem" in user_msgs


def test_enqueue_supervisor_message_routes_polluks_blocks_to_executor(monkeypatch, tmp_path: Path) -> None:
    """[Kastor -> Polluks] in notes should go to executor_log, not user panel."""

    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"
            self.memory_repository = type('Repo', (), {'recent_messages': lambda self, limit=6: []})()

        def ask(self, text: str, *, actor: str = "") -> str:
            return ""

    permission_manager = DummyPermissionManager()
    app = _AmiagiTextualApp(
        chat_service=cast(Any, _DummyChatService()),
        supervisor_dialogue_log_path=tmp_path / "supervision_dialogue.jsonl",
        permission_manager=cast(Any, permission_manager),
        shell_policy_path=tmp_path / "shell_allowlist.json",
    )

    logs: dict[str, list[str]] = {"user_model_log": [], "executor_log": [], "supervisor_log": []}
    monkeypatch.setattr(app, "_append_log", lambda widget_id, message: logs[widget_id].append(message))

    app._enqueue_supervisor_message(
        stage="user_turn",
        reason_code="IDENTITY_QUERY_HANDLED",
        notes="[Kastor -> Polluks] Popraw format odpowiedzi.",
        answer="",
    )

    # [Kastor -> Polluks] should go to executor_log, NOT user_model_log
    assert any("Popraw format" in msg for msg in logs["executor_log"])
    assert not any("Popraw format" in msg for msg in logs["user_model_log"])
