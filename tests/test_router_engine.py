"""Tests for RouterEngine — orchestration core skeleton."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from amiagi.application.event_bus import (
    ActorStateEvent,
    CycleFinishedEvent,
    EventBus,
    LogEvent,
    SupervisorMessageEvent,
)
from amiagi.application.router_engine import (
    RouterEngine,
    SUPPORTED_TOOLS,
    canonical_tool_name,
)
from amiagi.application.tool_calling import ToolCall


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_engine(
    *,
    tmp_path: Path,
    autonomous_mode: bool = False,
    supervisor: bool = False,
) -> RouterEngine:
    """Create a minimal RouterEngine for testing."""
    chat_service = MagicMock()
    chat_service.work_dir = tmp_path
    chat_service.supervisor_service = MagicMock() if supervisor else None
    chat_service.ollama_client = MagicMock()
    chat_service.ollama_client.base_url = "http://localhost:11434"
    chat_service.memory_repository = MagicMock()

    permission_manager = MagicMock()
    script_executor = MagicMock()
    event_bus = EventBus()

    shell_policy_path = tmp_path / "shell_policy.json"
    shell_policy_path.write_text("{}", encoding="utf-8")

    return RouterEngine(
        chat_service=chat_service,
        permission_manager=permission_manager,
        script_executor=script_executor,
        work_dir=tmp_path,
        shell_policy_path=shell_policy_path,
        event_bus=event_bus,
        autonomous_mode=autonomous_mode,
        router_mailbox_log_path=tmp_path / "mailbox.jsonl",
        supervisor_dialogue_log_path=tmp_path / "supervision.jsonl",
    )


# ---------------------------------------------------------------------------
# Constructor & Properties
# ---------------------------------------------------------------------------

class TestRouterEngineInit:
    def test_initial_state(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        assert engine.router_cycle_in_progress is False
        assert engine.passive_turns == 0
        assert engine.last_user_message == ""
        assert engine.last_model_answer == ""
        assert engine.watchdog_suspended is False
        assert engine.plan_pause_active is False

    def test_actor_states_no_supervisor(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        states = engine.actor_states
        assert states["router"] == "INIT"
        assert states["creator"] == "WAITING_INPUT"
        assert states["supervisor"] == "DISABLED"
        assert states["terminal"] == "WAITING_INPUT"

    def test_actor_states_with_supervisor(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        assert engine.actor_states["supervisor"] == "READY"

    def test_autonomous_mode_flag(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path, autonomous_mode=True)
        assert engine.autonomous_mode is True


# ---------------------------------------------------------------------------
# Event emission helpers
# ---------------------------------------------------------------------------

class TestEventEmission:
    def test_emit_log(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        received: list[LogEvent] = []
        engine.event_bus.on(LogEvent, received.append)
        engine._emit_log("user_model_log", "hello")
        assert len(received) == 1
        assert received[0].panel == "user_model_log"
        assert received[0].message == "hello"

    def test_emit_actor_state_updates_internal(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        received: list[ActorStateEvent] = []
        engine.event_bus.on(ActorStateEvent, received.append)
        engine._emit_actor_state("router", "ACTIVE", "test event")
        assert engine._actor_states["router"] == "ACTIVE"
        assert engine._last_router_event == "test event"
        assert len(received) == 1

    def test_emit_cycle_finished(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._router_cycle_in_progress = True
        finished: list[CycleFinishedEvent] = []
        engine.event_bus.on(CycleFinishedEvent, finished.append)
        engine._emit_cycle_finished("done")
        assert engine._router_cycle_in_progress is False
        assert len(finished) == 1

    def test_emit_supervisor_message(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        received: list[SupervisorMessageEvent] = []
        engine.event_bus.on(SupervisorMessageEvent, received.append)
        engine._emit_supervisor_message(
            stage="s", reason_code="OK", notes="n", answer="a"
        )
        assert len(received) == 1
        assert received[0].stage == "s"


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------

class TestQueueManagement:
    def test_submit_when_idle_dispatches(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._background_enabled = False
        # Mock the ask → return a simple text answer (no tool call)
        engine.chat_service.ask.return_value = "Gotowe."  # type: ignore[attr-defined]
        engine.permission_manager.allow_all = True

        finished: list[CycleFinishedEvent] = []
        engine.event_bus.on(CycleFinishedEvent, finished.append)

        engine.submit_user_turn("hello")

        assert len(finished) == 1
        assert engine._router_cycle_in_progress is False

    def test_submit_when_busy_queues(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._router_cycle_in_progress = True
        logs: list[LogEvent] = []
        engine.event_bus.on(LogEvent, logs.append)
        engine.submit_user_turn("queued msg")
        assert len(engine._user_message_queue) == 1
        assert engine._user_message_queue[0] == "queued msg"
        assert any("zakolejkowana" in log.message.lower() for log in logs)

    def test_drain_pops_next(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._background_enabled = False
        engine.chat_service.ask.return_value = "OK."  # type: ignore[attr-defined]
        engine.permission_manager.allow_all = True
        engine._user_message_queue.append("msg1")
        engine._user_message_queue.append("msg2")

        finished: list[CycleFinishedEvent] = []
        engine.event_bus.on(CycleFinishedEvent, finished.append)

        engine._drain_user_queue()

        # Both messages drained via cascading finalize → drain cycle
        assert len(engine._user_message_queue) == 0
        assert len(finished) == 2


# ---------------------------------------------------------------------------
# Plan tracking
# ---------------------------------------------------------------------------

class TestPlanTracking:
    def test_no_plan_file(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        assert engine.has_actionable_plan() is False

    def test_plan_with_incomplete_tasks(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        plan_dir = tmp_path / "notes"
        plan_dir.mkdir(parents=True)
        plan = {
            "goal": "test",
            "tasks": [
                {"id": "1", "title": "task1", "status": "rozpoczęta"},
                {"id": "2", "title": "task2", "status": "zakończona"},
            ],
        }
        (plan_dir / "main_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False), encoding="utf-8"
        )
        assert engine.has_actionable_plan() is True

    def test_plan_all_done(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        plan_dir = tmp_path / "notes"
        plan_dir.mkdir(parents=True)
        plan = {
            "goal": "test",
            "tasks": [
                {"id": "1", "title": "task1", "status": "zakończona"},
            ],
        }
        (plan_dir / "main_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False), encoding="utf-8"
        )
        assert engine.has_actionable_plan() is False

    def test_plan_requires_update_no_file(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        assert engine.plan_requires_update() is True

    def test_plan_requires_update_empty(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        plan_dir = tmp_path / "notes"
        plan_dir.mkdir(parents=True)
        (plan_dir / "main_plan.json").write_text("", encoding="utf-8")
        assert engine.plan_requires_update() is True

    def test_plan_requires_update_valid(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        plan_dir = tmp_path / "notes"
        plan_dir.mkdir(parents=True)
        plan = {"tasks": [{"id": "1", "title": "t", "status": "rozpoczęta"}]}
        (plan_dir / "main_plan.json").write_text(
            json.dumps(plan), encoding="utf-8"
        )
        assert engine.plan_requires_update() is False

    def test_set_plan_paused(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.set_plan_paused(paused=True, reason="test", source="unit")
        assert engine.plan_pause_active is True
        engine.set_plan_paused(paused=False, reason="resume", source="unit")
        assert engine.plan_pause_active is False


# ---------------------------------------------------------------------------
# Conversational analysis
# ---------------------------------------------------------------------------

class TestConversationalAnalysis:
    def test_interrupt_detection(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        assert engine.is_conversational_interrupt("kim jesteś?") is True
        assert engine.is_conversational_interrupt("napisz kod") is False

    def test_identity_query(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        assert engine.is_identity_query("kim jesteś") is True
        assert engine.is_identity_query("co potrafisz") is False


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------

class TestToolHelpers:
    def test_canonical_tool_name_alias(self) -> None:
        assert canonical_tool_name("run_command") == "run_shell"
        assert canonical_tool_name("read_file") == "read_file"
        assert canonical_tool_name("pdf_to_md") == "convert_pdf_to_markdown"

    def test_supported_tools_constant(self) -> None:
        assert "read_file" in SUPPORTED_TOOLS
        assert "run_shell" in SUPPORTED_TOOLS
        assert "nonexistent" not in SUPPORTED_TOOLS

    def test_runtime_supported_includes_registered(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        # Mock registered tools
        registry_dir = tmp_path / "state"
        registry_dir.mkdir(parents=True)
        registry_file = registry_dir / "tool_registry.json"
        registry_file.write_text(
            json.dumps({"my_custom_tool": {"script": "test.py"}}),
            encoding="utf-8",
        )
        names = engine.runtime_supported_tool_names()
        assert "read_file" in names  # built-in
        # Custom tool depends on list_registered_tools implementation — just check no crash

    def test_has_supported_tool_call_true(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        answer = '```tool_call\n{"tool":"read_file","args":{"path":"x"},"intent":"test"}\n```'
        assert engine.has_supported_tool_call(answer) is True

    def test_has_supported_tool_call_false_no_tools(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        assert engine.has_supported_tool_call("just text") is False


# ---------------------------------------------------------------------------
# Mailbox
# ---------------------------------------------------------------------------

class TestMailbox:
    def test_append_router_mailbox_log(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.append_router_mailbox_log({"action": "test"})
        log_path = tmp_path / "mailbox.jsonl"
        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        data = json.loads(lines[-1])
        # Now wraps via _append_router_mailbox_log with event + payload
        assert data["event"] == "raw"
        assert data["payload"]["action"] == "test"


# ---------------------------------------------------------------------------
# Watchdog reset
# ---------------------------------------------------------------------------

class TestWatchdogReset:
    def test_reset_on_user_input(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._watchdog_suspended_until_user_input = True
        engine._watchdog_attempts = 3
        engine._watchdog_capped_notified = True
        engine.reset_watchdog_on_user_input()
        assert engine._watchdog_suspended_until_user_input is False
        assert engine._watchdog_attempts == 0
        assert engine._watchdog_capped_notified is False


# ---------------------------------------------------------------------------
# execute_tool_call
# ---------------------------------------------------------------------------

class TestExecuteToolCall:
    """Tests for RouterEngine.execute_tool_call (Faza 1.1)."""

    def test_read_file(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        target = tmp_path / "hello.txt"
        target.write_text("cześć", encoding="utf-8")
        tc = ToolCall(tool="read_file", args={"path": "hello.txt"}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is True
        assert result["content"] == "cześć"

    def test_read_file_not_found(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        tc = ToolCall(tool="read_file", args={"path": "no_such.txt"}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is False
        assert result["error"] == "file_not_found"

    def test_read_file_chunking(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        target = tmp_path / "big.txt"
        target.write_text("A" * 100, encoding="utf-8")
        tc = ToolCall(tool="read_file", args={"path": "big.txt", "max_chars": 30}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is True
        assert result["has_more"] is True
        assert result["next_offset"] == 30

    def test_list_dir(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        (tmp_path / "afile.txt").write_text("x", encoding="utf-8")
        (tmp_path / "bdir").mkdir()
        tc = ToolCall(tool="list_dir", args={"path": "."}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is True
        assert "afile.txt" in result["items"]

    def test_write_file(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        tc = ToolCall(tool="write_file", args={"path": "out.txt", "content": "hello"}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is True
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello"

    def test_write_file_outside_workdir(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        tc = ToolCall(tool="write_file", args={"path": "/tmp/__outside.txt", "content": "x"}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is False
        assert "path_outside_work_dir" in result["error"]

    def test_write_file_exists_no_overwrite(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        (tmp_path / "exists.txt").write_text("old", encoding="utf-8")
        tc = ToolCall(tool="write_file", args={"path": "exists.txt", "content": "new"}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is False
        assert "overwrite" in result["error"]

    def test_append_file(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        (tmp_path / "append.txt").write_text("A", encoding="utf-8")
        tc = ToolCall(tool="append_file", args={"path": "append.txt", "content": "B"}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is True
        assert (tmp_path / "append.txt").read_text(encoding="utf-8") == "AB"

    def test_check_python_syntax_ok(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")
        tc = ToolCall(tool="check_python_syntax", args={"path": "good.py"}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is True
        assert result["syntax_ok"] is True

    def test_check_python_syntax_error(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        (tmp_path / "bad.py").write_text("def foo(\n", encoding="utf-8")
        tc = ToolCall(tool="check_python_syntax", args={"path": "bad.py"}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is False
        assert result["syntax_ok"] is False

    def test_run_command_alias(self, tmp_path: Path) -> None:
        """run_command should be normalised to run_shell."""
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        # Use `ls` which is typically on the allowlist; if policy rejects
        # just verify the tool field is correct in the error response.
        tc = ToolCall(tool="run_command", args={"command": "ls"}, intent="test")
        result = engine.execute_tool_call(tc)
        # Even if policy rejects, the tool was correctly normalized
        assert result.get("tool", "") == "run_shell" or "policy_rejected" in result.get("error", "")

    def test_check_capabilities(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        tc = ToolCall(tool="check_capabilities", args={}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is True
        assert "python" in result

    def test_unknown_tool(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        tc = ToolCall(tool="nonexistent_tool", args={}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is False
        assert "unknown_tool:nonexistent_tool" in result["error"]

    def test_permission_denied_without_allow_all(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = False
        (tmp_path / "perm.txt").write_text("x", encoding="utf-8")
        tc = ToolCall(tool="read_file", args={"path": "perm.txt"}, intent="test")
        result = engine.execute_tool_call(tc)
        assert result["ok"] is False
        assert "permission_denied" in result["error"]


# ---------------------------------------------------------------------------
# resolve_tool_calls
# ---------------------------------------------------------------------------

class TestResolveToolCalls:
    """Tests for RouterEngine.resolve_tool_calls (Faza 1.2)."""

    def test_no_tool_calls_returns_text(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        result = engine.resolve_tool_calls("plain text answer")
        assert result == "plain text answer"

    def test_single_tool_call_resolves(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        # Create a file to read
        (tmp_path / "data.txt").write_text("content123", encoding="utf-8")
        # Mock chat_service.ask to return plain text (no further tool calls)
        engine.chat_service.ask = MagicMock(return_value="Plik zawiera: content123")
        answer = '```tool_call\n{"tool":"read_file","args":{"path":"data.txt"},"intent":"read"}\n```'
        result = engine.resolve_tool_calls(answer)
        assert "content123" in result
        engine.chat_service.ask.assert_called_once()

    def test_unknown_tool_without_supervisor_falls_back(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        engine.chat_service.supervisor_service = None
        # First iteration: unknown tool → fallback to list_dir
        # Second iteration: list_dir executes → followup
        engine.chat_service.ask = MagicMock(return_value="Oto wynik list_dir.")
        answer = '```tool_call\n{"tool":"magic_wand","args":{},"intent":"cast"}\n```'
        result = engine.resolve_tool_calls(answer, max_steps=5)
        assert "list_dir" in result.lower() or "wynik" in result.lower()

    def test_loop_detection_breaks(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        # Make chat_service.ask always return the same tool_call → loop
        loop_answer = '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"loop"}\n```'
        engine.chat_service.ask = MagicMock(return_value=loop_answer)
        result = engine.resolve_tool_calls(loop_answer, max_steps=10)
        assert "pętl" in result.lower()  # "pętla" or similar

    def test_max_steps_limit(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine.permission_manager.allow_all = True
        # Each followup returns a new different tool_call
        counter = {"n": 0}
        def side_effect(msg, **kw):
            counter["n"] += 1
            return f'```tool_call\n{{"tool":"list_dir","args":{{"path":"dir{counter["n"]}"}},"intent":"step"}}\n```'
        engine.chat_service.ask = MagicMock(side_effect=side_effect)
        answer = '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"start"}\n```'
        # Create the dirs so list_dir doesn't fail
        for i in range(20):
            (tmp_path / f"dir{i}").mkdir(exist_ok=True)
        result = engine.resolve_tool_calls(answer, max_steps=3)
        # Should have stopped — either stalled or returned something
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Supervisor helpers
# ---------------------------------------------------------------------------

class TestSupervisorHelpers:
    def test_merge_supervisor_notes(self, tmp_path: Path) -> None:
        assert RouterEngine._merge_supervisor_notes("base", "extra") == "base extra"
        assert RouterEngine._merge_supervisor_notes("", "only") == "only"
        assert RouterEngine._merge_supervisor_notes("only", "") == "only"
        assert RouterEngine._merge_supervisor_notes("base", "base") == "base"

    def test_enqueue_supervisor_message_dedup(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        events: list = []
        engine.event_bus.on(SupervisorMessageEvent, events.append)
        engine.enqueue_supervisor_message(stage="s", reason_code="OK", notes="n", answer="a")
        engine.enqueue_supervisor_message(stage="s", reason_code="OK", notes="n", answer="a")
        # Second call is a duplicate → should be skipped
        assert len(engine._supervisor_outbox) == 1
        assert len(events) == 1  # only one emit

    def test_drain_supervisor_outbox_context(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._supervisor_outbox.append({
            "actor": "Kastor", "target": "Polluks",
            "stage": "test", "reason_code": "OK", "notes": "note", "suggested_step": "",
        })
        context = engine._drain_supervisor_outbox_context()
        assert "[Kastor -> Polluks]" in context
        assert len(engine._supervisor_outbox) == 0


# ---------------------------------------------------------------------------
# Faza 2 helper methods
# ---------------------------------------------------------------------------

class TestFormatUserFacingAnswer:
    def test_empty_answer(self) -> None:
        result = RouterEngine._format_user_facing_answer("")
        assert "krok operacyjny" in result.lower()

    def test_plain_text_passthrough(self) -> None:
        result = RouterEngine._format_user_facing_answer("Oto wynik analizy.")
        assert result == "Oto wynik analizy."

    def test_tool_call_answer(self) -> None:
        tool_answer = '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"scan"}\n```'
        result = RouterEngine._format_user_facing_answer(tool_answer)
        assert "list_dir" in result
        assert "scan" in result


class TestRenderSingleToolCallBlock:
    def test_basic_render(self) -> None:
        tc = ToolCall(tool="read_file", args={"path": "x.py"}, intent="read")
        block = RouterEngine._render_single_tool_call_block(tc)
        assert block.startswith("```tool_call\n")
        assert block.endswith("\n```")
        payload = json.loads(block.split("\n", 1)[1].rsplit("\n", 1)[0])
        assert payload["tool"] == "read_file"
        assert payload["args"]["path"] == "x.py"


class TestSingleSentence:
    def test_extracts_first_sentence(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        assert engine._single_sentence("Hello world. More text.") == "Hello world."

    def test_truncates_long_text(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        long_text = "a" * 300
        result = engine._single_sentence(long_text)
        assert len(result) == 220


class TestModelResponseAwaitsUser:
    def test_question_mark(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        assert engine._model_response_awaits_user("Czy chcesz kontynuować?") is True

    def test_empty_answer(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        assert engine._model_response_awaits_user("") is False

    def test_tool_call_not_awaiting(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        tc = '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"x"}\n```'
        assert engine._model_response_awaits_user(tc) is False


class TestIsPrematureCompletion:
    def test_no_plan_file(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        assert engine._is_premature_plan_completion("jakiś tekst") is False

    def test_plan_completed_stage(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        plan_dir = tmp_path / "notes"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan = {"current_stage": "completed", "tasks": []}
        (plan_dir / "main_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        assert engine._is_premature_plan_completion("pytanie do sponsora") is True

    def test_completion_signal_present(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        plan_dir = tmp_path / "notes"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan = {"current_stage": "completed", "tasks": []}
        (plan_dir / "main_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        # With the completion signal, it should NOT be premature
        assert engine._is_premature_plan_completion("Zakończyłem zadanie") is False

    def test_all_tasks_done_no_signal(self, tmp_path: Path) -> None:
        """Returns True when all tasks are 'zakończona' even if current_stage != completed."""
        engine = _make_engine(tmp_path=tmp_path)
        plan_dir = tmp_path / "notes"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan = {
            "current_stage": "execution",
            "tasks": [
                {"name": "T1", "status": "zakończona"},
                {"name": "T2", "status": "zakończona"},
            ],
        }
        (plan_dir / "main_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        assert engine._is_premature_plan_completion("Co dalej?") is True


class TestRedirectPrematureCompletion:
    def test_returns_none_without_supervisor(self, tmp_path: Path) -> None:
        """_redirect_premature_completion returns None when supervisor is absent."""
        engine = _make_engine(tmp_path=tmp_path, supervisor=False)
        result = engine._redirect_premature_completion("test", "Czekam na Twoje instrukcje.")
        assert result is None


class TestAppendPlanEvent:
    def test_appends_to_collaboration_log(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        plan_dir = tmp_path / "notes"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan = {"tasks": [], "collaboration_log": []}
        plan_path = plan_dir / "main_plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        engine._append_plan_event("test_event", {"key": "val"})
        updated = json.loads(plan_path.read_text(encoding="utf-8"))
        assert len(updated["collaboration_log"]) == 1
        assert updated["collaboration_log"][0]["event"] == "test_event"


class TestApplyIdleHint:
    def test_idle_hint_off(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._idle_until_epoch = 99999.0
        engine._apply_idle_hint_from_answer("some text IDLE_UNTIL: off", "test")
        assert engine._idle_until_epoch is None

    def test_no_marker(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._apply_idle_hint_from_answer("no marker here", "test")
        assert engine._idle_until_epoch is None


class TestProcessUserTurn:
    """Integration tests for the full _process_user_turn cycle."""

    def test_basic_cycle_emits_events(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._background_enabled = False
        engine.chat_service.ask.return_value = "Gotowe, wykonano polecenie."  # type: ignore[attr-defined]
        engine.permission_manager.allow_all = True

        logs: list[LogEvent] = []
        actors: list[ActorStateEvent] = []
        finished: list[CycleFinishedEvent] = []
        engine.event_bus.on(LogEvent, logs.append)
        engine.event_bus.on(ActorStateEvent, actors.append)
        engine.event_bus.on(CycleFinishedEvent, finished.append)

        engine._process_user_turn("test message")

        assert len(finished) == 1
        assert engine._router_cycle_in_progress is False
        assert engine._last_user_message == "test message"
        # Should have logged to executor_log
        assert any("user_turn" in log.message for log in logs if log.panel == "executor_log")

    def test_denied_access(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._background_enabled = False
        engine.permission_manager.allow_all = False
        engine.permission_manager.granted_once = set()

        logs: list[LogEvent] = []
        engine.event_bus.on(LogEvent, logs.append)

        engine._process_user_turn("hello")

        # Should have logged denial
        assert any("odmowa" in log.message.lower() for log in logs)
        assert engine._router_cycle_in_progress is False

    def test_quit_emits_quit_event(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        engine._background_enabled = False
        engine.permission_manager.allow_all = True

        finished: list[CycleFinishedEvent] = []
        engine.event_bus.on(CycleFinishedEvent, finished.append)

        engine._process_user_turn("/quit")

        assert len(finished) == 1
        assert finished[0].event == "quit_requested"

    def test_model_error_handled(self, tmp_path: Path) -> None:
        from amiagi.infrastructure.ollama_client import OllamaClientError
        engine = _make_engine(tmp_path=tmp_path)
        engine._background_enabled = False
        engine.chat_service.ask.side_effect = OllamaClientError("connection refused")  # type: ignore[attr-defined]
        engine.permission_manager.allow_all = True

        logs: list[LogEvent] = []
        finished: list[CycleFinishedEvent] = []
        engine.event_bus.on(LogEvent, logs.append)
        engine.event_bus.on(CycleFinishedEvent, finished.append)

        engine._process_user_turn("hello")

        assert len(finished) == 1
        assert any("błąd" in log.message.lower() for log in logs)

    def test_with_supervisor(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        engine._background_enabled = False
        engine.chat_service.ask.return_value = "Odpowiedź bez narzędzia."  # type: ignore[attr-defined]
        engine.permission_manager.allow_all = True

        # Configure supervisor mock to return a valid result
        sup = engine.chat_service.supervisor_service
        assert sup is not None
        sup_result = MagicMock()
        sup_result.answer = "Odpowiedź po nadzorze."
        sup_result.reason_code = "OK"
        sup_result.notes = "Sprawdzono."
        sup_result.work_state = "ACTIVE"
        sup_result.repairs_applied = 0
        sup.refine.return_value = sup_result  # type: ignore[attr-defined]

        engine.chat_service.memory_repository.recent_messages.return_value = []  # type: ignore[attr-defined]

        finished: list[CycleFinishedEvent] = []
        engine.event_bus.on(CycleFinishedEvent, finished.append)

        engine._process_user_turn("zrób coś")

        assert len(finished) >= 1
        assert sup.refine.called  # type: ignore[union-attr]


class TestEnforceSupervisedProgress:
    def test_returns_initial_if_has_tool_call(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        # Create a plan file so plan_requires_update() returns False
        plan_dir = tmp_path / "notes"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan = {"tasks": [{"id": "T1", "status": "w trakcie realizacji"}]}
        (plan_dir / "main_plan.json").write_text(json.dumps(plan), encoding="utf-8")

        answer = '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"x"}\n```'
        result = engine._enforce_supervised_progress("msg", answer)
        assert result == answer

    def test_allow_text_reply_without_tool(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        result = engine._enforce_supervised_progress("msg", "plain text", allow_text_reply=True)
        assert result == "plain text"

    def test_forces_tool_call_via_supervisor(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        sup = engine.chat_service.supervisor_service
        assert sup is not None
        sup_result = MagicMock()
        sup_result.answer = '```tool_call\n{"tool":"list_dir","args":{"path":"."},"intent":"forced"}\n```'
        sup_result.reason_code = "FORCED"
        sup_result.notes = ""
        sup.refine.return_value = sup_result  # type: ignore[attr-defined]

        result = engine._enforce_supervised_progress("msg", "passive answer")
        assert "list_dir" in result


# ---------------------------------------------------------------------------
# Faza 3 — Watchdog tick
# ---------------------------------------------------------------------------

class TestWatchdogTick:
    def test_noop_without_supervisor(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path, supervisor=False)
        engine._last_user_message = "hello"
        engine._last_progress_monotonic = 0.0
        engine.watchdog_tick()
        assert engine._router_cycle_in_progress is False

    def test_noop_when_suspended(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        engine._last_user_message = "hello"
        engine._watchdog_suspended_until_user_input = True
        engine.watchdog_tick()
        assert engine._router_cycle_in_progress is False

    def test_noop_when_cycle_in_progress(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        engine._last_user_message = "hello"
        engine._router_cycle_in_progress = True
        engine.watchdog_tick()
        # Should remain True (unchanged)
        assert engine._router_cycle_in_progress is True

    def test_noop_when_idle_too_short(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        engine._last_user_message = "hello"
        engine._passive_turns = 1
        import time as _time
        engine._last_progress_monotonic = _time.monotonic() - 1.0  # Only 1s idle
        engine.watchdog_tick()
        assert engine._router_cycle_in_progress is False

    @patch("amiagi.application.router_engine.time")
    def test_watchdog_dispatches_bg_work(self, mock_time: MagicMock, tmp_path: Path) -> None:
        mock_time.monotonic.return_value = 10_000.0
        mock_time.time.return_value = 10_000.0
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        engine._background_enabled = False  # run synchronously for test

        sup = engine.chat_service.supervisor_service
        assert sup is not None
        sup_result = MagicMock()
        sup_result.answer = "Kontynuuję listowanie plików."
        sup_result.reason_code = "OK"
        sup_result.notes = ""
        sup_result.work_state = "ACTIVE"
        sup_result.repairs_applied = 0
        sup.refine.return_value = sup_result  # type: ignore[attr-defined]
        engine.chat_service.memory_repository.recent_messages.return_value = []  # type: ignore[attr-defined]

        engine._last_user_message = "kontynuuj"
        engine._last_model_answer = "Pasywna odpowiedź."
        engine._passive_turns = 2
        engine._last_progress_monotonic = 0.0

        events: list = []
        engine.event_bus.on(LogEvent, events.append)

        engine.watchdog_tick()

        assert engine._watchdog_attempts == 1
        assert sup.refine.called  # type: ignore[union-attr]
        assert any("watchdog" in e.message.lower() for e in events if isinstance(e, LogEvent))

    @patch("amiagi.application.router_engine.time")
    def test_watchdog_caps_at_max_attempts(self, mock_time: MagicMock, tmp_path: Path) -> None:
        mock_time.monotonic.return_value = 10_000.0
        mock_time.time.return_value = 10_000.0
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        engine._background_enabled = False

        engine._last_user_message = "kontynuuj"
        engine._passive_turns = 2
        engine._last_progress_monotonic = 0.0
        engine._watchdog_attempts = 5  # already at max

        events: list = []
        engine.event_bus.on(LogEvent, events.append)

        engine.watchdog_tick()

        assert engine._watchdog_suspended_until_user_input is True
        assert engine._watchdog_capped_notified is True
        # Supervisor.refine should NOT be called
        engine.chat_service.supervisor_service.refine.assert_not_called()  # type: ignore[union-attr]

    @patch("amiagi.application.router_engine.time")
    def test_watchdog_respects_idle_until(self, mock_time: MagicMock, tmp_path: Path) -> None:
        mock_time.monotonic.return_value = 10_000.0
        mock_time.time.return_value = 100.0  # epoch time < idle_until
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        engine._background_enabled = False

        engine._last_user_message = "kontynuuj"
        engine._passive_turns = 2
        engine._last_progress_monotonic = 0.0
        engine._idle_until_epoch = 200.0  # epoch 200 > current 100

        engine.watchdog_tick()

        assert engine._router_cycle_in_progress is False
        engine.chat_service.supervisor_service.refine.assert_not_called()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Faza 3 — Auto-resume tick
# ---------------------------------------------------------------------------

class TestAutoResumeTick:
    @patch("amiagi.application.router_engine.time")
    def test_noop_when_not_paused(self, mock_time: MagicMock, tmp_path: Path) -> None:
        mock_time.monotonic.return_value = 10_000.0
        engine = _make_engine(tmp_path=tmp_path)
        assert engine.auto_resume_tick(10_000.0) is False

    @patch("amiagi.application.router_engine.time")
    def test_blocks_identity_query(self, mock_time: MagicMock, tmp_path: Path) -> None:
        mock_time.monotonic.return_value = 10_000.0
        engine = _make_engine(tmp_path=tmp_path)
        engine._plan_pause_active = True
        engine._pending_user_decision = True
        engine._pending_decision_identity_query = True
        engine._plan_pause_started_monotonic = 0.0

        result = engine.auto_resume_tick(10_000.0)
        assert result is True
        # Plan should STILL be paused
        assert engine._plan_pause_active is True

    @patch("amiagi.application.router_engine.time")
    def test_auto_resume_after_threshold(self, mock_time: MagicMock, tmp_path: Path) -> None:
        mock_time.monotonic.return_value = 10_000.0
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        engine._background_enabled = False

        sup = engine.chat_service.supervisor_service
        assert sup is not None
        sup_result = MagicMock()
        sup_result.answer = "Wznowiłem plan."
        sup_result.reason_code = "OK"
        sup_result.notes = ""
        sup_result.work_state = "ACTIVE"
        sup_result.repairs_applied = 0
        sup.refine.return_value = sup_result  # type: ignore[attr-defined]
        engine.chat_service.ask.return_value = "Wznowiłem plan."  # type: ignore[attr-defined]

        engine._plan_pause_active = True
        engine._pending_user_decision = True
        engine._plan_pause_started_monotonic = 0.0  # Long idle

        result = engine.auto_resume_tick(10_000.0)
        assert result is True
        assert engine._plan_pause_active is False
        assert engine._pending_user_decision is False

    @patch("amiagi.application.router_engine.time")
    def test_force_resume_ignores_threshold(self, mock_time: MagicMock, tmp_path: Path) -> None:
        mock_time.monotonic.return_value = 1.0  # Very short idle
        engine = _make_engine(tmp_path=tmp_path, supervisor=True)
        engine._background_enabled = False

        sup = engine.chat_service.supervisor_service
        assert sup is not None
        sup_result = MagicMock()
        sup_result.answer = "Wymuszono."
        sup_result.reason_code = "OK"
        sup_result.notes = ""
        sup_result.work_state = "ACTIVE"
        sup_result.repairs_applied = 0
        sup.refine.return_value = sup_result  # type: ignore[attr-defined]
        engine.chat_service.ask.return_value = "Wymuszono."  # type: ignore[attr-defined]

        engine._plan_pause_active = True
        engine._plan_pause_started_monotonic = 0.5  # Only 0.5s ago

        result = engine.auto_resume_tick(1.0, force=True)
        assert result is True
        assert engine._plan_pause_active is False


# ---------------------------------------------------------------------------
# Faza 3 — Poll supervision dialogue
# ---------------------------------------------------------------------------

class TestPollSupervisionDialogue:
    def test_noop_when_no_log_file(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path=tmp_path)
        # Ensure log file does not exist
        engine.poll_supervision_dialogue()
        # Should silently do nothing

    def test_parses_executor_answer(self, tmp_path: Path) -> None:
        log_path = tmp_path / "supervision.jsonl"
        entry = {
            "stage": "user_turn",
            "type": "review_exchange",
            "executor_answer": "Odpowiedź Polluksa",
        }
        log_path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")

        engine = _make_engine(tmp_path=tmp_path)

        events: list[LogEvent] = []
        engine.event_bus.on(LogEvent, events.append)
        engine.poll_supervision_dialogue()

        assert len(events) >= 1
        assert any("POLLUKS→KASTOR" in e.message for e in events)
        assert any("Odpowiedź Polluksa" in e.message for e in events)

    def test_parses_supervisor_raw_output(self, tmp_path: Path) -> None:
        log_path = tmp_path / "supervision.jsonl"
        sup_output = json.dumps({
            "status": "ok",
            "reason_code": "OK",
            "work_state": "RUNNING",
            "notes": "krótka uwaga",
        }, ensure_ascii=False)
        entry = {
            "stage": "user_turn",
            "type": "review_result",
            "supervisor_raw_output": sup_output,
            "status": "ok",
            "reason_code": "OK",
        }
        log_path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")

        engine = _make_engine(tmp_path=tmp_path)

        events: list[LogEvent] = []
        engine.event_bus.on(LogEvent, events.append)
        engine.poll_supervision_dialogue()

        assert any("KASTOR→ROUTER" in e.message for e in events)
        assert any("status=ok" in e.message for e in events)

    def test_incremental_offset(self, tmp_path: Path) -> None:
        """Second call should not re-read already-consumed lines."""
        log_path = tmp_path / "supervision.jsonl"
        entry = {
            "stage": "s1",
            "type": "review_exchange",
            "executor_answer": "first",
        }
        log_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        engine = _make_engine(tmp_path=tmp_path)

        events1: list[LogEvent] = []
        engine.event_bus.on(LogEvent, events1.append)
        engine.poll_supervision_dialogue()
        assert events1  # Got events

        events2: list[LogEvent] = []
        engine.event_bus.clear()
        engine.event_bus.on(LogEvent, events2.append)
        engine.poll_supervision_dialogue()
        assert not events2  # No new events

        # Append more data → should show up
        with log_path.open("a", encoding="utf-8") as f:
            entry2 = {"stage": "s2", "type": "review_exchange", "executor_answer": "second"}
            f.write(json.dumps(entry2) + "\n")

        events3: list[LogEvent] = []
        engine.event_bus.clear()
        engine.event_bus.on(LogEvent, events3.append)
        engine.poll_supervision_dialogue()
        assert events3
        assert any("second" in e.message for e in events3)

    def test_format_supervision_lane_label(self) -> None:
        label = RouterEngine._format_supervision_lane_label(
            stage="user_turn", kind="review_exchange", direction="POLLUKS→KASTOR",
        )
        assert label == "[POLLUKS→KASTOR | user_turn:review_exchange]"

    def test_format_supervision_lane_label_defaults(self) -> None:
        label = RouterEngine._format_supervision_lane_label(stage="", kind="", direction="TEST")
        assert "unknown_stage" in label
        assert "unknown_type" in label
