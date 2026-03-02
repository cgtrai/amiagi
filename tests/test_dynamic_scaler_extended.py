"""Tests for DynamicScaler.apply_scale — actual agent spawn/terminate."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from amiagi.application.dynamic_scaler import DynamicScaler, ScaleEvent
from amiagi.domain.agent import AgentDescriptor, AgentRole, AgentState


# ---- fake registry ----


class FakeRegistry:
    """Mimics AgentRegistry for testing."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentDescriptor] = {}

    def register(self, descriptor: AgentDescriptor) -> None:
        self._agents[descriptor.agent_id] = descriptor

    def unregister(self, agent_id: str) -> AgentDescriptor:
        return self._agents.pop(agent_id)

    def list_all(self) -> list[AgentDescriptor]:
        return list(self._agents.values())

    def __len__(self) -> int:
        return len(self._agents)


# ---- fake factory ----


class FakeFactory:
    """Mimics AgentFactory for testing."""

    _counter: int = 0

    def __init__(self, registry: FakeRegistry) -> None:
        self._registry = registry

    @staticmethod
    def generate_id() -> str:
        FakeFactory._counter += 1
        return f"fake-{FakeFactory._counter:04d}"

    def create_agent(
        self,
        descriptor: AgentDescriptor,
        *,
        client: Any = None,
    ) -> None:
        self._registry.register(descriptor)


# ====================================================================
# Tests
# ====================================================================


def test_apply_scale_up_spawns_agent() -> None:
    reg = FakeRegistry()
    factory = FakeFactory(reg)
    scaler = DynamicScaler()

    event = ScaleEvent(direction="up", agent_role="backend", team_id="t1")
    agent_id = scaler.apply_scale(event, registry=reg, factory=factory)

    assert agent_id is not None
    assert len(reg) == 1
    agent = reg.list_all()[0]
    assert agent.metadata.get("scaled") is True
    assert agent.metadata.get("team_id") == "t1"


def test_apply_scale_down_terminates_scaled_agent() -> None:
    reg = FakeRegistry()
    # Pre-register a scaled agent
    d = AgentDescriptor(
        agent_id="temp-001",
        name="temp",
        role=AgentRole.EXECUTOR,
        metadata={"scaled": True},
    )
    reg.register(d)
    assert len(reg) == 1

    scaler = DynamicScaler()
    event = ScaleEvent(direction="down", agent_role="general")
    removed_id = scaler.apply_scale(event, registry=reg)

    assert removed_id == "temp-001"
    assert len(reg) == 0
    assert d.state == AgentState.TERMINATED


def test_apply_scale_down_prefers_scaled_agents() -> None:
    reg = FakeRegistry()
    # One permanent, one scaled
    perm = AgentDescriptor(
        agent_id="perm-001", name="Perm", role=AgentRole.EXECUTOR,
    )
    scaled = AgentDescriptor(
        agent_id="scaled-001", name="Scaled", role=AgentRole.EXECUTOR,
        metadata={"scaled": True},
    )
    reg.register(perm)
    reg.register(scaled)

    scaler = DynamicScaler()
    event = ScaleEvent(direction="down")
    removed = scaler.apply_scale(event, registry=reg)

    assert removed == "scaled-001"
    assert len(reg) == 1
    assert reg.list_all()[0].agent_id == "perm-001"


def test_apply_scale_down_no_idle_returns_none() -> None:
    reg = FakeRegistry()
    busy = AgentDescriptor(
        agent_id="busy-001", name="Busy", role=AgentRole.EXECUTOR,
        state=AgentState.WORKING,
    )
    reg.register(busy)

    scaler = DynamicScaler()
    event = ScaleEvent(direction="down")
    removed = scaler.apply_scale(event, registry=reg)

    assert removed is None
    assert len(reg) == 1


def test_apply_scale_no_registry_returns_none() -> None:
    scaler = DynamicScaler()
    event = ScaleEvent(direction="up")
    assert scaler.apply_scale(event) is None


def test_apply_scale_up_no_factory_returns_none() -> None:
    reg = FakeRegistry()
    scaler = DynamicScaler()
    event = ScaleEvent(direction="up")
    assert scaler.apply_scale(event, registry=reg) is None


def test_evaluate_and_apply_integration() -> None:
    """Integration: evaluate → get event → apply_scale."""
    reg = FakeRegistry()
    factory = FakeFactory(reg)
    scaler = DynamicScaler(scale_up_threshold=3, cooldown_seconds=0)

    event = scaler.evaluate(pending_tasks=5, active_agents=1, team_id="t1")
    assert event is not None
    assert event.direction == "up"

    agent_id = scaler.apply_scale(event, registry=reg, factory=factory)
    assert agent_id is not None
    assert len(reg) == 1
