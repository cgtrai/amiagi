from __future__ import annotations

from pathlib import Path
from typing import Any, cast
import json

from amiagi.application.chat_service import ChatService
from amiagi.application.supervisor_service import SupervisionResult
from amiagi.interfaces import cli as cli_module


class FakePermissionManager:
    def request_local_network(self, reason: str) -> bool:
        _ = reason
        return True

    def request_internet(self, reason: str) -> bool:
        _ = reason
        return True

    def request_disk_read(self, reason: str) -> bool:
        _ = reason
        return True

    def request_disk_write(self, reason: str) -> bool:
        _ = reason
        return True

    def request_process_exec(self, reason: str) -> bool:
        _ = reason
        return True

    def request_camera_access(self, reason: str) -> bool:
        _ = reason
        return True

    def request_microphone_access(self, reason: str) -> bool:
        _ = reason
        return True


class TrackingPermissionManager:
    last_instance: "TrackingPermissionManager | None" = None

    def __init__(self) -> None:
        self.allow_all = False
        self.local_network_requests = 0
        TrackingPermissionManager.last_instance = self

    def request_local_network(self, reason: str) -> bool:
        _ = reason
        self.local_network_requests += 1
        return self.allow_all

    def request_internet(self, reason: str) -> bool:
        _ = reason
        return self.allow_all

    def request_disk_read(self, reason: str) -> bool:
        _ = reason
        return self.allow_all

    def request_disk_write(self, reason: str) -> bool:
        _ = reason
        return self.allow_all

    def request_process_exec(self, reason: str) -> bool:
        _ = reason
        return self.allow_all


class FakeOllamaClient:
    base_url = "http://127.0.0.1:11434"
    queue_policy = None
    vram_advisor = None


class FakeChatService:
    def __init__(self, work_dir: Path, responses: list[str]) -> None:
        self.work_dir = work_dir
        self._responses = list(responses)
        self.ask_calls = 0
        self.ollama_client = FakeOllamaClient()
        self.activity_logger = None
        self.supervisor_service: Any | None = None

    def bootstrap_runtime_readiness(self) -> str:
        return "Gotowy."

    def ask(self, user_message: str) -> str:
        _ = user_message
        self.ask_calls += 1
        if not self._responses:
            return "Brak odpowiedzi"
        return self._responses.pop(0)


class ToolFlowInjectingSupervisor:
    def __init__(self) -> None:
        self.tool_flow_calls = 0

    def refine(self, *, user_message: str, model_answer: str, stage: str) -> SupervisionResult:
        _ = user_message
        if stage == "tool_flow":
            self.tool_flow_calls += 1
            return SupervisionResult(
                answer='```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"forced_by_supervisor"}\n```',
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


class WaitingDecisionSupervisor:
    def refine(self, *, user_message: str, model_answer: str, stage: str) -> SupervisionResult:
        _ = (user_message, stage)
        return SupervisionResult(
            answer=model_answer,
            repairs_applied=0,
            status="ok",
            reason_code="OK",
            work_state="WAITING_USER_DECISION",
        )


class FakeTTYInput:
    def __init__(self, lines: list[str]) -> None:
        self._lines = iter(lines)

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        return next(self._lines, "")


def test_run_cli_does_not_print_none_and_forces_tool_flow(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "notes.txt").write_text("x", encoding="utf-8")

    responses = [
        "None",
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"scan"}\n```',
        "Wykonano listowanie.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Model> None" not in output
    assert "Model> Wykonano listowanie." in output


def test_run_cli_does_not_leak_unresolved_python_pseudo_tool_call(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    responses = [
        """```python
write_file(
    path=\"hello_world.py\",
    content=\"print('Hello, World!')\",
    overwrite=true
)
```""",
        """```python
write_file(
    path=\"hello_world.py\",
    content=\"print('Hello, World!')\",
    overwrite=true
)
```""",
        """```python
write_file(
    path=\"hello_world.py\",
    content=\"print('Hello, World!')\",
    overwrite=true
)
```""",
        """```python
write_file(
    path=\"hello_world.py\",
    content=\"print('Hello, World!')\",
    overwrite=true
)
```""",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "write_file(" not in output
    assert "Nie udało się uzyskać poprawnego wywołania narzędzia frameworka" not in output
    assert "Model> Brak odpowiedzi" in output


def test_run_cli_rejects_unknown_tool_in_autonomy_corrective(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "a.txt").write_text("x", encoding="utf-8")

    responses = [
        "Brak akcji.",
        '```tool_call\n{"tool":"google_search","args":{"query":"x"},"intent":"search"}\n```',
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"scan"}\n```',
        "Wykonano listowanie.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "unknown_tool:google_search" not in output
    assert "Model> Wykonano listowanie." in output


def test_run_cli_blocks_write_file_outside_work_dir(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    outside_target = tmp_path / "outside.txt"

    responses = [
        f'```tool_call\n{{"tool":"write_file","args":{{"path":"{outside_target}","content":"x","overwrite":true}},"intent":"test_outside_write"}}\n```',
        "Zapis odrzucony przez politykę.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    _ = capsys.readouterr().out
    assert outside_target.exists() is False


def test_run_cli_allows_main_plan_overwrite_without_explicit_flag(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    plan_path = work_dir / "notes" / "main_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text('{"goal":"old"}', encoding="utf-8")

    new_payload = '{"goal":"updated","current_stage":"research","tasks":[]}'
    responses = [
        "```tool_call\n"
        + json.dumps(
            {
                "tool": "write_file",
                "args": {"path": "notes/main_plan.json", "content": new_payload},
                "intent": "update_plan",
            },
            ensure_ascii=False,
        )
        + "\n```",
        "Plan zaktualizowany.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    _ = capsys.readouterr().out
    assert plan_path.read_text(encoding="utf-8") == new_payload


def test_run_cli_accepts_write_file_data_payload_for_main_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    plan_path = work_dir / "notes" / "main_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text('{"goal":"old"}', encoding="utf-8")

    data_payload = {
        "goal": "Zgromadzenie interesujących tematów",
        "key_achievement": "Zainicjowanie procesu badania",
        "current_stage": "rozpoczęta",
        "tasks": [
            {
                "id": "T1",
                "title": "Określenie obszarów zainteresowań",
                "status": "rozpoczęta",
            }
        ],
    }

    responses = [
        "```tool_call\n"
        + json.dumps(
            {
                "tool": "write_file",
                "args": {
                    "path": "notes/main_plan.json",
                    "data": data_payload,
                },
                "intent": "update_plan_with_data_payload",
            },
            ensure_ascii=False,
        )
        + "\n```",
        "Plan zaktualizowany.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    _ = capsys.readouterr().out
    loaded = json.loads(plan_path.read_text(encoding="utf-8"))
    assert loaded["goal"] == "Zgromadzenie interesujących tematów"
    assert loaded["tasks"][0]["id"] == "T1"


def test_run_cli_still_requires_overwrite_for_other_existing_files(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    target_path = work_dir / "notes" / "other.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text('{"value":"old"}', encoding="utf-8")

    responses = [
        "```tool_call\n"
        + json.dumps(
            {
                "tool": "write_file",
                "args": {"path": "notes/other.json", "content": '{"value":"new"}'},
                "intent": "update_other",
            },
            ensure_ascii=False,
        )
        + "\n```",
        "Próba zapisu gotowa.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    _ = capsys.readouterr().out
    assert target_path.read_text(encoding="utf-8") == '{"value":"old"}'


def test_run_cli_blocks_empty_json_write(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    target_path = work_dir / "state" / "research_log.jsonl"

    responses = [
        '```tool_call\n{"tool":"write_file","args":{"path":"state/research_log.jsonl","content":""},"intent":"write_empty_jsonl"}\n```',
        "Zapis odrzucony.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    _ = capsys.readouterr().out
    assert target_path.exists() is False


def test_run_cli_allows_empty_text_write(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    target_path = work_dir / "notes" / "empty.txt"

    responses = [
        '```tool_call\n{"tool":"write_file","args":{"path":"notes/empty.txt","content":""},"intent":"write_empty_txt"}\n```',
        "Zapis wykonany.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    _ = capsys.readouterr().out
    assert target_path.exists() is True
    assert target_path.read_text(encoding="utf-8") == ""


def test_run_cli_autonomous_mode_enables_global_permissions(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    responses = [
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"scan"}\n```',
        "Odpowiedź w trybie autonomicznym.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", TrackingPermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
        autonomous_mode=True,
    )

    output = capsys.readouterr().out
    assert "Model> Odpowiedź w trybie autonomicznym." in output
    instance = TrackingPermissionManager.last_instance
    assert instance is not None
    assert instance.allow_all is True
    assert instance.local_network_requests >= 1


def test_run_cli_capabilities_command_prints_capability_snapshot(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    chat_service = FakeChatService(work_dir=work_dir, responses=[])

    user_inputs = iter(["/capabilities", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "--- CAPABILITIES ---" in output
    assert '"tool": "check_capabilities"' in output


def test_run_cli_blocks_run_python_outside_work_dir(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    marker = tmp_path / "framework_marker.txt"
    outside_script = tmp_path / "outside_runner.py"
    outside_script.write_text(
        f"from pathlib import Path\nPath(r'{marker}').write_text('changed', encoding='utf-8')\n",
        encoding="utf-8",
    )

    responses = [
        f'```tool_call\n{{"tool":"run_python","args":{{"path":"{outside_script}","args":[]}},"intent":"unsafe"}}\n```',
        "Uruchomienie zablokowane przez politykę bezpieczeństwa.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    _ = capsys.readouterr().out
    assert marker.exists() is False


def test_run_cli_does_not_start_tool_flow_for_plain_text_answer(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    responses = [
        "To zwykła odpowiedź bez działań narzędziowych.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)
    supervisor = ToolFlowInjectingSupervisor()
    chat_service.supervisor_service = supervisor

    user_inputs = iter(["hej", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Model> To zwykła odpowiedź bez działań narzędziowych." in output
    assert chat_service.ask_calls == 1
    assert supervisor.tool_flow_calls == 0


def test_run_cli_reactivates_after_idle_timeout_without_new_user_message(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    responses = [
        "Plan gotowy, ale bez kroku narzędziowego.",
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"idle_reactivation"}\n```',
        "Auto-reaktywacja zakończona.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)
    monkeypatch.setattr(cli_module, "_IDLE_REACTIVATION_SECONDS", 0.0)

    fake_tty = FakeTTYInput(["ok\n", "/exit\n"])
    monkeypatch.setattr(cli_module.sys, "stdin", fake_tty)

    select_outcomes = iter([True, False, True])

    def fake_select(read, write, err, timeout):
        _ = (write, err, timeout)
        is_ready = next(select_outcomes)
        if is_ready:
            return (read, [], [])
        return ([], [], [])

    monkeypatch.setattr(cli_module.select, "select", fake_select)
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Model> Auto-reaktywacja zakończona." in output
    assert chat_service.ask_calls == 3


def test_run_cli_does_not_reactivate_when_waiting_user_decision(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    responses = [
        "Czy chcesz kontynuować i wybrać wariant realizacji?",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)
    chat_service.supervisor_service = WaitingDecisionSupervisor()
    monkeypatch.setattr(cli_module, "_IDLE_REACTIVATION_SECONDS", 0.0)

    fake_tty = FakeTTYInput(["ok\n", "/exit\n"])
    monkeypatch.setattr(cli_module.sys, "stdin", fake_tty)

    select_outcomes = iter([True, False, True])

    def fake_select(read, write, err, timeout):
        _ = (write, err, timeout)
        is_ready = next(select_outcomes)
        if is_ready:
            return (read, [], [])
        return ([], [], [])

    monkeypatch.setattr(cli_module.select, "select", fake_select)
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Model> Czy chcesz kontynuować i wybrać wariant realizacji?" in output
    assert chat_service.ask_calls == 1


def test_run_cli_caps_idle_reactivation_after_two_attempts(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    responses = [
        "Brak kroku operacyjnego po decyzji.",
        "Nadal bez realnego działania.",
        "Wciąż bez tool_call.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)
    monkeypatch.setattr(cli_module, "_IDLE_REACTIVATION_SECONDS", 0.0)

    fake_tty = FakeTTYInput(["ok\n", "/exit\n"])
    monkeypatch.setattr(cli_module.sys, "stdin", fake_tty)

    select_outcomes = iter([True, False, False, False, True])

    def fake_select(read, write, err, timeout):
        _ = (write, err, timeout)
        is_ready = next(select_outcomes)
        if is_ready:
            return (read, [], [])
        return ([], [], [])

    monkeypatch.setattr(cli_module.select, "select", fake_select)
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Wstrzymuję kolejne autowzbudzenia po 2 próbach." in output
    assert chat_service.ask_calls == 3


def test_run_cli_safe_fallback_executes_next_tool_call_instead_of_printing_it(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "wprowadzenie.md").write_text("kontekst", encoding="utf-8")

    responses = [
        "```python\nwrite_file(path='a.py', content='print(1)', overwrite=true)\n```",
        "```python\nwrite_file(path='a.py', content='print(1)', overwrite=true)\n```",
        "```python\nwrite_file(path='a.py', content='print(1)', overwrite=true)\n```",
        '```tool_call\n{"tool":"read_file","args":{"path":"wprowadzenie.md"},"intent":"post_fallback_read"}\n```',
        "Finalna odpowiedź po automatycznym domknięciu fallbacku.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Finalna odpowiedź po automatycznym domknięciu fallbacku." in output
    assert '"tool":"read_file"' not in output


def test_run_cli_aborts_code_path_after_repeated_syntax_failures(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    responses = [
        '```tool_call\n{"tool":"write_file","args":{"path":"broken.py","content":"def x(:\\n    pass","overwrite":true},"intent":"create_broken_file"}\n```',
        '```tool_call\n{"tool":"check_python_syntax","args":{"path":"broken.py"},"intent":"first_check"}\n```',
        '```tool_call\n{"tool":"check_python_syntax","args":{"path":"broken.py"},"intent":"second_check"}\n```',
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"continue_main_goal"}\n```',
        "Kontynuuję główny cel bez dalszego generowania kodu.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Kontynuuję główny cel bez dalszego generowania kodu." in output


def test_run_cli_detects_goal_phrase_confirms_and_starts_planning(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    responses = [
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"start_planning"}\n```',
        "Plan celu uruchomiony.",
        '```tool_call\n{"tool":"write_file","args":{"path":"notes/main_plan.json","data":{"goal":"zebranie wiarygodnych informacji prawnych o AI w Polsce","key_achievement":"Rozpoczęto planowanie","current_stage":"research_start","tasks":[{"id":"T1","title":"Skompletować źródła prawne","status":"rozpoczęta","next_step":"Wyszukać akty prawne"}]}} ,"intent":"persist_plan"}\n```',
        "Plan celu uruchomiony.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter([
        "Twoim celem jest zebranie wiarygodnych informacji prawnych o AI w Polsce",
        "tak",
        "/exit",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Czy dobrze rozumiem, że moim głównym celem jest" in output
    assert "Zarejestrowano główny cel" in output
    assert "Model> Plan celu uruchomiony." in output

    plan_path = work_dir / "notes" / "main_plan.json"
    assert plan_path.exists() is True
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert "zebranie wiarygodnych informacji prawnych o AI w Polsce" in payload.get("goal", "")


def test_run_cli_goal_confirmation_resets_stale_plan_progress_for_new_goal(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    notes_dir = work_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "main_plan.json").write_text(
        json.dumps(
            {
                "goal": "Demonstrate basic Python execution",
                "key_achievement": "Created and validated hello_script.py",
                "current_stage": "validation_complete",
                "tasks": [
                    {
                        "id": "T1",
                        "title": "Create hello world script",
                        "status": "zakończona",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    responses = [
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"start_planning"}\n```',
        "Plan celu uruchomiony.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter([
        "Twoim celem jest zebrać informacje prawne o AI w Polsce",
        "tak",
        "/goal-status",
        "/exit",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "goal_reset_required" in output
    assert "0/0 zakończonych" in output

    payload = json.loads((notes_dir / "main_plan.json").read_text(encoding="utf-8"))
    assert payload.get("goal") == "zebrać informacje prawne o AI w Polsce"
    assert payload.get("tasks") == []
    assert payload.get("key_achievement") == ""


def test_run_cli_goal_confirmation_enforces_plan_persistence_when_missing_tasks(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)

    plan_payload = {
        "goal": "Zebrać informacje prawne o AI w Polsce",
        "key_achievement": "Rozpoczęto planowanie",
        "current_stage": "research_start",
        "tasks": [
            {
                "id": "T1",
                "title": "Skompletować źródła prawne",
                "status": "rozpoczęta",
                "next_step": "Wyszukać akty prawne",
            }
        ],
    }

    responses = [
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"start_planning"}\n```',
        "Plan celu uruchomiony.",
        "```tool_call\n"
        + json.dumps(
            {
                "tool": "write_file",
                "args": {
                    "path": "notes/main_plan.json",
                    "data": plan_payload,
                },
                "intent": "persist_plan_after_planning",
            },
            ensure_ascii=False,
        )
        + "\n```",
        "Plan zapisany trwale.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter([
        "Twoim celem jest zebrać informacje prawne o AI w Polsce",
        "tak",
        "/goal-status",
        "/exit",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Model> Plan zapisany trwale." in output
    assert "0/1 zakończonych" in output

    persisted = json.loads((work_dir / "notes" / "main_plan.json").read_text(encoding="utf-8"))
    assert isinstance(persisted.get("tasks"), list)
    assert len(persisted["tasks"]) == 1


def test_run_cli_hard_gate_enforces_plan_progress_update_after_stale_turns(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    notes_dir = work_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "main_plan.json").write_text(
        json.dumps(
            {
                "goal": "Zebrać informacje prawne o AI",
                "key_achievement": "Etap startowy",
                "current_stage": "research_start",
                "tasks": [
                    {
                        "id": "T1",
                        "title": "Skompletować źródła",
                        "status": "w trakcie realizacji",
                        "next_step": "Wykonać kolejne wyszukiwanie",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    updated_plan = {
        "goal": "Zebrać informacje prawne o AI",
        "key_achievement": "Zebrano kolejne źródła",
        "current_stage": "research_refinement",
        "tasks": [
            {
                "id": "T1",
                "title": "Skompletować źródła",
                "status": "zakończona",
                "next_step": "Przejść do analizy",
            }
        ],
    }

    responses = [
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"step_1"}\n```',
        "Krok 1 wykonany.",
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"step_2"}\n```',
        "Krok 2 wykonany.",
        "```tool_call\n"
        + json.dumps(
            {
                "tool": "write_file",
                "args": {
                    "path": "notes/main_plan.json",
                    "data": updated_plan,
                },
                "intent": "refresh_plan_progress",
            },
            ensure_ascii=False,
        )
        + "\n```",
        "Plan postępu zaktualizowany.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "kontynuuj", "/goal-status", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Model> Plan postępu zaktualizowany." in output
    assert "research_refinement" in output
    assert "1/1 zakończonych" in output


def test_run_cli_user_turn_enforces_plan_persistence_when_tasks_missing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    notes_dir = work_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "main_plan.json").write_text(
        json.dumps(
            {
                "goal": "Zebrać informacje prawne o AI",
                "key_achievement": "",
                "current_stage": "goal_reset_required",
                "tasks": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    persisted_plan = {
        "goal": "Zebrać informacje prawne o AI",
        "key_achievement": "Rozpoczęto systematyczne badanie",
        "current_stage": "research_start",
        "tasks": [
            {
                "id": "T1",
                "title": "Skompletować źródła prawne",
                "status": "rozpoczęta",
                "next_step": "Wykonać kolejne wyszukiwanie",
            }
        ],
    }

    responses = [
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"step_1"}\n```',
        "Krok wykonany.",
        "```tool_call\n"
        + json.dumps(
            {
                "tool": "write_file",
                "args": {
                    "path": "notes/main_plan.json",
                    "data": persisted_plan,
                },
                "intent": "persist_plan_after_user_turn",
            },
            ensure_ascii=False,
        )
        + "\n```",
        "Plan utrwalony po turze użytkownika.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    user_inputs = iter(["kontynuuj", "/goal-status", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Model> Plan utrwalony po turze użytkownika." in output
    assert "0/1 zakończonych" in output

    persisted = json.loads((work_dir / "notes" / "main_plan.json").read_text(encoding="utf-8"))
    assert isinstance(persisted.get("tasks"), list)
    assert len(persisted["tasks"]) == 1


def test_run_cli_record_microphone_clip_auto_selects_device_and_profile(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / "artifacts" / "mic_auto.wav"

    responses = [
        '```tool_call\n{"tool":"record_microphone_clip","args":{"output_path":"artifacts/mic_auto.wav","duration_seconds":2},"intent":"mic_auto"}\n```',
        "Nagranie zakończone.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)

    class DummyCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    attempted_profiles: list[tuple[int, int]] = []

    def fake_run(command, check=False, capture_output=True, text=True, timeout=0):
        _ = (check, capture_output, text, timeout)
        executable = str(command[0]) if command else ""
        if executable.endswith("arecord") and "-l" in command:
            return DummyCompleted(
                0,
                stdout=(
                    "card 0: PCH [HDA Intel PCH], device 0: ALC1220 Analog [ALC1220 Analog]\n"
                    "card 2: C920 [HD Pro Webcam C920], device 0: USB Audio [USB Audio]\n"
                ),
            )

        if executable.endswith("arecord"):
            rate = int(command[command.index("-r") + 1]) if "-r" in command else 0
            channels = int(command[command.index("-c") + 1]) if "-c" in command else 0
            attempted_profiles.append((rate, channels))
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if (rate, channels) == (16000, 1):
                return DummyCompleted(1, stderr="Channels count non available")

            output_path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
            return DummyCompleted(0)

        return DummyCompleted(0)

    def fake_which(name: str):
        if name == "arecord":
            return "/usr/bin/arecord"
        return None

    user_inputs = iter(["kontynuuj", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)
    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_module.shutil, "which", fake_which)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Model> Nagranie zakończone." in output
    assert "System> [MIC] Przygotowanie nagrywania mikrofonu." in output
    assert "System> [MIC] Nagrywanie aktywne." in output
    assert "System> [MIC] Nagrywanie zakończone." in output
    assert target.exists() is True
    assert (16000, 1) in attempted_profiles
    assert any(profile != (16000, 1) for profile in attempted_profiles)


def test_run_cli_goal_status_command_shows_plan_snapshot(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    notes_dir = work_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "main_plan.json").write_text(
        json.dumps(
            {
                "goal": "Zbadać ramy prawne AI w Polsce",
                "key_achievement": "Raport zgodności",
                "current_stage": "research_web_legal",
                "tasks": [
                    {"id": "T1", "status": "zakończona"},
                    {"id": "T2", "status": "w trakcie realizacji"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    chat_service = FakeChatService(work_dir=work_dir, responses=[])
    user_inputs = iter(["/goal-status", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "--- GOAL STATUS ---" in output
    assert "Zbadać ramy prawne AI w Polsce" in output
    assert "research_web_legal" in output
    assert "1/2 zakończonych" in output


def test_run_cli_goal_alias_shows_plan_snapshot(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    notes_dir = work_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "main_plan.json").write_text(
        json.dumps(
            {
                "goal": "Zweryfikować alias komendy celu",
                "key_achievement": "",
                "current_stage": "alias_check",
                "tasks": [
                    {"id": "T1", "status": "w trakcie realizacji"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    chat_service = FakeChatService(work_dir=work_dir, responses=[])
    user_inputs = iter(["/goal", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "--- GOAL STATUS ---" in output
    assert "Zweryfikować alias komendy celu" in output
    assert "alias_check" in output


def test_run_cli_goal_status_auto_repairs_malformed_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    notes_dir = work_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    malformed = """{
    \"goal\": \"Zrozumieć ramy prawne AI\",
    \"current_stage\": \"inicjalizacja\",
    \"tasks\": [
        {\"id\": \"T1\", \"status\": \"rozpoczęta\"}
    ]
)"""
    (notes_dir / "main_plan.json").write_text(malformed, encoding="utf-8")

    chat_service = FakeChatService(work_dir=work_dir, responses=[])
    user_inputs = iter(["/goal-status", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(user_inputs))
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "auto_repair: true" in output
    assert "parse_error: true" not in output
    assert "Zrozumieć ramy prawne AI" in output
    assert "inicjalizacja" in output

    repaired_payload = json.loads((notes_dir / "main_plan.json").read_text(encoding="utf-8"))
    assert repaired_payload.get("goal") == "Zrozumieć ramy prawne AI"
    assert repaired_payload.get("current_stage") == "inicjalizacja"
    assert (notes_dir / "main_plan.json.broken").exists() is True


def test_run_cli_reactivates_on_idle_when_actionable_plan_exists_even_without_passive_turns(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    work_dir = tmp_path / "amiagi-my-work"
    notes_dir = work_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "main_plan.json").write_text(
        json.dumps(
            {
                "goal": "Dowieźć główny raport",
                "current_stage": "research",
                "tasks": [
                    {"id": "T1", "status": "w trakcie realizacji"},
                    {"id": "T2", "status": "rozpoczęta"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    responses = [
        '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"resume_main_plan"}\n```',
        "Wznowiono realizację głównego planu.",
    ]
    chat_service = FakeChatService(work_dir=work_dir, responses=responses)
    monkeypatch.setattr(cli_module, "_IDLE_REACTIVATION_SECONDS", 0.0)

    fake_tty = FakeTTYInput(["/goal-status\n", "/exit\n"])
    monkeypatch.setattr(cli_module.sys, "stdin", fake_tty)

    select_outcomes = iter([True, False, True])

    def fake_select(read, write, err, timeout):
        _ = (write, err, timeout)
        is_ready = next(select_outcomes)
        if is_ready:
            return (read, [], [])
        return ([], [], [])

    monkeypatch.setattr(cli_module.select, "select", fake_select)
    monkeypatch.setattr(cli_module, "PermissionManager", FakePermissionManager)

    shell_policy_path = Path(__file__).resolve().parents[1] / "config" / "shell_allowlist.json"
    cli_module.run_cli(
        chat_service=cast(ChatService, chat_service),
        shell_policy_path=shell_policy_path,
    )

    output = capsys.readouterr().out
    assert "Model> Wznowiono realizację głównego planu." in output
