"""Tests for agent domain models — AgentDescriptor, AgentState, AgentRole."""

from __future__ import annotations

import pytest

from amiagi.domain.agent import AgentDescriptor, AgentRole, AgentState


class TestAgentState:
    def test_all_states_exist(self) -> None:
        assert set(AgentState) == {
            AgentState.IDLE,
            AgentState.WORKING,
            AgentState.PAUSED,
            AgentState.ERROR,
            AgentState.TERMINATED,
        }

    def test_state_values_are_lowercase(self) -> None:
        for state in AgentState:
            assert state.value == state.value.lower()


class TestAgentRole:
    def test_all_roles_exist(self) -> None:
        assert set(AgentRole) == {
            AgentRole.EXECUTOR,
            AgentRole.SUPERVISOR,
            AgentRole.SPECIALIST,
        }


class TestAgentDescriptor:
    def _make_descriptor(self, **kwargs) -> AgentDescriptor:
        defaults = {
            "agent_id": "test-001",
            "name": "TestAgent",
            "role": AgentRole.EXECUTOR,
        }
        defaults.update(kwargs)
        return AgentDescriptor(**defaults)

    def test_default_state_is_idle(self) -> None:
        d = self._make_descriptor()
        assert d.state == AgentState.IDLE

    def test_transition_idle_to_working(self) -> None:
        d = self._make_descriptor()
        d.transition_to(AgentState.WORKING)
        assert d.state == AgentState.WORKING

    def test_transition_working_to_idle(self) -> None:
        d = self._make_descriptor()
        d.transition_to(AgentState.WORKING)
        d.transition_to(AgentState.IDLE)
        assert d.state == AgentState.IDLE

    def test_transition_idle_to_paused(self) -> None:
        d = self._make_descriptor()
        d.transition_to(AgentState.PAUSED)
        assert d.state == AgentState.PAUSED

    def test_transition_paused_to_idle(self) -> None:
        d = self._make_descriptor()
        d.transition_to(AgentState.PAUSED)
        d.transition_to(AgentState.IDLE)
        assert d.state == AgentState.IDLE

    def test_transition_working_to_error(self) -> None:
        d = self._make_descriptor()
        d.transition_to(AgentState.WORKING)
        d.transition_to(AgentState.ERROR)
        assert d.state == AgentState.ERROR

    def test_transition_error_to_idle(self) -> None:
        d = self._make_descriptor()
        d.transition_to(AgentState.WORKING)
        d.transition_to(AgentState.ERROR)
        d.transition_to(AgentState.IDLE)
        assert d.state == AgentState.IDLE

    def test_transition_to_terminated(self) -> None:
        d = self._make_descriptor()
        d.transition_to(AgentState.TERMINATED)
        assert d.state == AgentState.TERMINATED

    def test_terminated_is_final(self) -> None:
        d = self._make_descriptor()
        d.transition_to(AgentState.TERMINATED)
        with pytest.raises(ValueError, match="cannot transition"):
            d.transition_to(AgentState.IDLE)

    def test_invalid_transition_raises(self) -> None:
        d = self._make_descriptor()
        with pytest.raises(ValueError):
            d.transition_to(AgentState.ERROR)  # IDLE → ERROR not allowed

    def test_can_transition_to(self) -> None:
        d = self._make_descriptor()
        assert d.can_transition_to(AgentState.WORKING) is True
        assert d.can_transition_to(AgentState.ERROR) is False

    def test_default_fields(self) -> None:
        d = self._make_descriptor()
        assert d.persona_prompt == ""
        assert d.model_backend == "ollama"
        assert d.model_name == ""
        assert d.skills == []
        assert d.tools == []
        assert d.metadata == {}
        assert d.created_at is not None

    def test_custom_fields(self) -> None:
        d = self._make_descriptor(
            persona_prompt="I am a tester",
            model_backend="openai",
            model_name="gpt-4o",
            skills=["python", "testing"],
            tools=["read_file"],
            metadata={"origin": "test"},
        )
        assert d.persona_prompt == "I am a tester"
        assert d.model_backend == "openai"
        assert d.model_name == "gpt-4o"
        assert d.skills == ["python", "testing"]
        assert d.tools == ["read_file"]
        assert d.metadata["origin"] == "test"
