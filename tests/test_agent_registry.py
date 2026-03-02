"""Tests for AgentRegistry — CRUD, state transitions, lifecycle events."""

from __future__ import annotations

import threading

import pytest

from amiagi.application.agent_registry import AgentRegistry
from amiagi.domain.agent import AgentDescriptor, AgentRole, AgentState
from amiagi.infrastructure.lifecycle_logger import LifecycleLogger


def _make_descriptor(agent_id: str = "a1", **kwargs) -> AgentDescriptor:
    defaults = {"agent_id": agent_id, "name": f"Agent-{agent_id}", "role": AgentRole.EXECUTOR}
    defaults.update(kwargs)
    return AgentDescriptor(**defaults)


class TestAgentRegistryCRUD:
    def test_register_and_get(self) -> None:
        registry = AgentRegistry()
        desc = _make_descriptor("a1")
        registry.register(desc)
        assert registry.get("a1") is desc

    def test_register_duplicate_raises(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_descriptor("a1"))
        with pytest.raises(KeyError, match="already registered"):
            registry.register(_make_descriptor("a1"))

    def test_get_missing_returns_none(self) -> None:
        registry = AgentRegistry()
        assert registry.get("nonexistent") is None

    def test_unregister(self) -> None:
        registry = AgentRegistry()
        desc = _make_descriptor("a1")
        registry.register(desc)
        removed = registry.unregister("a1")
        assert removed is desc
        assert registry.get("a1") is None

    def test_unregister_missing_raises(self) -> None:
        registry = AgentRegistry()
        with pytest.raises(KeyError):
            registry.unregister("nonexistent")

    def test_list_all(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_descriptor("a1"))
        registry.register(_make_descriptor("a2"))
        assert len(registry.list_all()) == 2

    def test_len(self) -> None:
        registry = AgentRegistry()
        assert len(registry) == 0
        registry.register(_make_descriptor("a1"))
        assert len(registry) == 1

    def test_contains(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_descriptor("a1"))
        assert "a1" in registry
        assert "a2" not in registry

    def test_list_by_role(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_descriptor("e1", role=AgentRole.EXECUTOR))
        registry.register(_make_descriptor("s1", role=AgentRole.SUPERVISOR))
        registry.register(_make_descriptor("e2", role=AgentRole.EXECUTOR))
        executors = registry.list_by_role(AgentRole.EXECUTOR)
        assert len(executors) == 2

    def test_list_by_state(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_descriptor("a1"))
        registry.register(_make_descriptor("a2"))
        registry.update_state("a2", AgentState.WORKING)
        idle = registry.list_by_state(AgentState.IDLE)
        assert len(idle) == 1
        assert idle[0].agent_id == "a1"


class TestAgentRegistryStateTransitions:
    def test_update_state_valid(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_descriptor("a1"))
        registry.update_state("a1", AgentState.WORKING)
        assert registry.get("a1").state == AgentState.WORKING

    def test_update_state_invalid_raises(self) -> None:
        registry = AgentRegistry()
        registry.register(_make_descriptor("a1"))
        with pytest.raises(ValueError):
            registry.update_state("a1", AgentState.ERROR)

    def test_update_state_missing_raises(self) -> None:
        registry = AgentRegistry()
        with pytest.raises(KeyError, match="not found"):
            registry.update_state("no-such", AgentState.IDLE)


class TestAgentRegistryLifecycleEvents:
    def test_register_emits_event(self, tmp_path) -> None:
        log_path = tmp_path / "lifecycle.jsonl"
        logger = LifecycleLogger(log_path)
        registry = AgentRegistry(lifecycle_logger=logger)
        registry.register(_make_descriptor("a1"))
        content = log_path.read_text()
        assert "agent.registered" in content
        assert "a1" in content

    def test_state_change_emits_event(self, tmp_path) -> None:
        log_path = tmp_path / "lifecycle.jsonl"
        logger = LifecycleLogger(log_path)
        registry = AgentRegistry(lifecycle_logger=logger)
        registry.register(_make_descriptor("a1"))
        registry.update_state("a1", AgentState.WORKING, reason="test")
        content = log_path.read_text()
        assert "agent.state_changed" in content

    def test_unregister_emits_event(self, tmp_path) -> None:
        log_path = tmp_path / "lifecycle.jsonl"
        logger = LifecycleLogger(log_path)
        registry = AgentRegistry(lifecycle_logger=logger)
        registry.register(_make_descriptor("a1"))
        registry.unregister("a1")
        content = log_path.read_text()
        assert "agent.unregistered" in content


class TestAgentRegistryConcurrency:
    def test_concurrent_registrations(self) -> None:
        registry = AgentRegistry()
        errors: list[Exception] = []

        def register_agent(i: int) -> None:
            try:
                registry.register(_make_descriptor(f"agent-{i}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=register_agent, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(registry) == 50
