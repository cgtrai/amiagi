"""Tests for AgentRegistry.list_active() method."""

from __future__ import annotations

from amiagi.application.agent_registry import AgentRegistry
from amiagi.domain.agent import AgentDescriptor, AgentRole, AgentState


def _make_descriptor(agent_id: str, state: AgentState = AgentState.IDLE) -> AgentDescriptor:
    d = AgentDescriptor(
        agent_id=agent_id,
        name=f"Agent-{agent_id}",
        role=AgentRole.EXECUTOR,
    )
    d.state = state  # Direct assignment for testing
    return d


def test_list_active_empty() -> None:
    registry = AgentRegistry()
    assert registry.list_active() == []


def test_list_active_includes_idle() -> None:
    registry = AgentRegistry()
    d = AgentDescriptor(agent_id="a1", name="idle_agent", role=AgentRole.EXECUTOR)
    registry.register(d)
    active = registry.list_active()
    assert len(active) == 1
    assert active[0].agent_id == "a1"


def test_list_active_includes_working() -> None:
    registry = AgentRegistry()
    d = AgentDescriptor(agent_id="a1", name="working_agent", role=AgentRole.EXECUTOR)
    registry.register(d)
    registry.update_state("a1", AgentState.WORKING)
    active = registry.list_active()
    assert len(active) == 1


def test_list_active_excludes_paused() -> None:
    registry = AgentRegistry()
    d = AgentDescriptor(agent_id="a1", name="paused_agent", role=AgentRole.EXECUTOR)
    registry.register(d)
    registry.update_state("a1", AgentState.PAUSED)
    active = registry.list_active()
    assert len(active) == 0


def test_list_active_excludes_terminated() -> None:
    registry = AgentRegistry()
    d = AgentDescriptor(agent_id="a1", name="term_agent", role=AgentRole.EXECUTOR)
    registry.register(d)
    registry.update_state("a1", AgentState.TERMINATED)
    active = registry.list_active()
    assert len(active) == 0


def test_list_active_excludes_error() -> None:
    registry = AgentRegistry()
    d = AgentDescriptor(agent_id="a1", name="err_agent", role=AgentRole.EXECUTOR)
    registry.register(d)
    # IDLE -> WORKING -> ERROR (IDLE -> ERROR is not a valid transition)
    registry.update_state("a1", AgentState.WORKING)
    registry.update_state("a1", AgentState.ERROR)
    active = registry.list_active()
    assert len(active) == 0


def test_list_active_mixed_states() -> None:
    registry = AgentRegistry()

    for aid, name in [("a1", "idle"), ("a2", "working"), ("a3", "paused"), ("a4", "terminated")]:
        registry.register(AgentDescriptor(agent_id=aid, name=name, role=AgentRole.EXECUTOR))

    registry.update_state("a2", AgentState.WORKING)
    registry.update_state("a3", AgentState.PAUSED)
    registry.update_state("a4", AgentState.TERMINATED)

    active = registry.list_active()
    active_ids = {a.agent_id for a in active}
    assert active_ids == {"a1", "a2"}
