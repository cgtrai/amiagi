from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from amiagi.interfaces.textual_cli import (
    _AmiagiTextualApp,
    _CommandOutcome,
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
    assert "Komendy:" in merged
    assert "Komendy (textual):" in merged
    assert "/permissions" in merged
    assert "/run-shell" in merged


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

        def ask(self, text: str) -> str:
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
    assert "Twórca:" in merged
    assert "Nadzorca:" in merged
    assert "Terminal:" in merged


def test_textual_idle_until_command_sets_and_clears_schedule(tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummyChatService:
        def __init__(self) -> None:
            self.ollama_client = _DummyOllamaClient()
            self.supervisor_service = None
            self.work_dir = tmp_path / "work"

        def ask(self, text: str) -> str:
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

        def ask(self, text: str) -> str:
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

        def ask(self, text: str) -> str:
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

        def ask(self, text: str) -> str:
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

        def ask(self, text: str) -> str:
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

        def refine(self, *, user_message: str, model_answer: str, stage: str) -> SupervisionResult:
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

        def ask(self, text: str) -> str:
            assert text == "kontynuuj"
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
    monkeypatch.setattr(app, "_enforce_supervised_progress", lambda _text, answer: answer)
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

        def refine(self, *, user_message: str, model_answer: str, stage: str) -> SupervisionResult:
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

        def ask(self, text: str) -> str:
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
    monkeypatch.setattr(app, "_enforce_supervised_progress", lambda _text, answer: answer)
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
            self.ask_payloads: list[str] = []

        def ask(self, text: str) -> str:
            self.ask_payloads.append(text)
            if text == "kontynuuj":
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
    monkeypatch.setattr(app, "_enforce_supervised_progress", lambda _text, answer: answer)

    event = SimpleNamespace(value="kontynuuj", input=SimpleNamespace(value="x"))
    app.on_input_submitted(cast(Any, event))

    assert len(chat_service.ask_payloads) == 2
    assert chat_service.ask_payloads[0] == "kontynuuj"
    assert chat_service.ask_payloads[1].startswith("[TOOL_RESULT]")
    assert logs["user_model_log"][-1] == "Model: Wykonano krok i mogę kontynuować."


def test_textual_unknown_tool_is_corrected_by_supervisor(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.stages: list[str] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str) -> SupervisionResult:
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
            self.ask_payloads: list[str] = []

        def ask(self, text: str) -> str:
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

    assert "textual_unknown_tool_corrective" in supervisor.stages
    assert any(payload.startswith("[TOOL_RESULT]") for payload in chat_service.ask_payloads)
    assert logs["user_model_log"][-1] == "Model: Naprawiono i wykonano krok."


def test_textual_progress_guard_enforces_plan_or_action(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str) -> SupervisionResult:
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

        def ask(self, text: str) -> str:
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


def test_textual_watchdog_nudges_supervisor_after_inactivity(monkeypatch, tmp_path: Path) -> None:
    class _DummyOllamaClient:
        base_url = "http://127.0.0.1:11434"

    class _DummySupervisor:
        def __init__(self) -> None:
            self.stages: list[str] = []

        def refine(self, *, user_message: str, model_answer: str, stage: str) -> SupervisionResult:
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

        def ask(self, text: str) -> str:
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

        def refine(self, *, user_message: str, model_answer: str, stage: str) -> SupervisionResult:
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

        def ask(self, text: str) -> str:
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
