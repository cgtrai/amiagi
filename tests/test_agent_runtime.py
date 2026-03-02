"""Tests for AgentRuntime — lifecycle hooks, state management, ask()."""

from __future__ import annotations

import pytest

from amiagi.domain.agent import AgentDescriptor, AgentRole, AgentState
from amiagi.infrastructure.agent_runtime import AgentRuntime


def _make_runtime(
    agent_id: str = "rt-1",
    chat_service=None,
    supervisor_service=None,
) -> AgentRuntime:
    descriptor = AgentDescriptor(
        agent_id=agent_id,
        name=f"Runtime-{agent_id}",
        role=AgentRole.EXECUTOR,
    )
    return AgentRuntime(
        descriptor=descriptor,
        chat_service=chat_service,
        supervisor_service=supervisor_service,
    )


class FakeChatService:
    """Minimal stub matching ChatService.ask() signature."""

    def __init__(self, response: str = "ok") -> None:
        self.response = response
        self.calls: list[str] = []

    def ask(self, user_message: str, *, actor: str = "Sponsor") -> str:
        self.calls.append(user_message)
        return self.response


class FailingChatService:
    """Stub that raises on ask()."""

    def ask(self, user_message: str, *, actor: str = "Sponsor") -> str:
        raise RuntimeError("model failure")


class TestAgentRuntimeProperties:
    def test_agent_id(self) -> None:
        rt = _make_runtime("x1")
        assert rt.agent_id == "x1"

    def test_initial_state_idle(self) -> None:
        rt = _make_runtime()
        assert rt.state == AgentState.IDLE


class TestAgentRuntimeAsk:
    def test_ask_success(self) -> None:
        svc = FakeChatService("hello")
        rt = _make_runtime(chat_service=svc)
        result = rt.ask("test prompt")
        assert result == "hello"
        assert svc.calls == ["test prompt"]
        assert rt.state == AgentState.IDLE

    def test_ask_no_chat_service_raises(self) -> None:
        rt = _make_runtime()
        with pytest.raises(RuntimeError, match="no chat_service"):
            rt.ask("hello")

    def test_ask_error_transitions_to_error_state(self) -> None:
        svc = FailingChatService()
        rt = _make_runtime(chat_service=svc)
        error_fired = []
        rt.on_error.append(lambda r: error_fired.append(True))
        with pytest.raises(RuntimeError, match="model failure"):
            rt.ask("hello")
        assert rt.state == AgentState.ERROR
        assert error_fired == [True]

    def test_ask_while_paused_raises(self) -> None:
        svc = FakeChatService()
        rt = _make_runtime(chat_service=svc)
        rt.pause()
        with pytest.raises(RuntimeError, match="cannot answer"):
            rt.ask("hello")


class TestAgentRuntimeLifecycle:
    def test_pause_and_resume(self) -> None:
        rt = _make_runtime()
        rt.pause()
        assert rt.state == AgentState.PAUSED
        rt.resume()
        assert rt.state == AgentState.IDLE

    def test_terminate(self) -> None:
        rt = _make_runtime()
        rt.terminate()
        assert rt.state == AgentState.TERMINATED

    def test_terminate_is_final(self) -> None:
        rt = _make_runtime()
        rt.terminate()
        with pytest.raises(ValueError):
            rt.pause()


class TestAgentRuntimeHooks:
    def test_spawn_hook(self) -> None:
        rt = _make_runtime()
        spawned = []
        rt.on_spawn.append(lambda r: spawned.append(r.agent_id))
        rt.spawn()
        assert spawned == [rt.agent_id]

    def test_pause_hook(self) -> None:
        rt = _make_runtime()
        paused = []
        rt.on_pause.append(lambda r: paused.append(True))
        rt.pause()
        assert paused == [True]

    def test_resume_hook(self) -> None:
        rt = _make_runtime()
        rt.pause()
        resumed = []
        rt.on_resume.append(lambda r: resumed.append(True))
        rt.resume()
        assert resumed == [True]

    def test_terminate_hook(self) -> None:
        rt = _make_runtime()
        terminated = []
        rt.on_terminate.append(lambda r: terminated.append(True))
        rt.terminate()
        assert terminated == [True]

    def test_failing_hook_does_not_break_runtime(self) -> None:
        rt = _make_runtime()
        rt.on_pause.append(lambda r: 1 / 0)  # will raise ZeroDivisionError
        # Should not raise
        rt.pause()
        assert rt.state == AgentState.PAUSED
