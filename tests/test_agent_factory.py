"""Tests for AgentFactory — create_agent, create_from_existing."""

from __future__ import annotations

from pathlib import Path

import pytest

from amiagi.application.agent_factory import AgentFactory
from amiagi.application.agent_registry import AgentRegistry
from amiagi.domain.agent import AgentDescriptor, AgentRole, AgentState
from amiagi.infrastructure.lifecycle_logger import LifecycleLogger
from amiagi.infrastructure.memory_repository import MemoryRepository


class FakeClient:
    """Minimal stub matching ChatCompletionClient protocol."""

    model = "fake-model"

    def chat(self, messages, system_prompt="", num_ctx=None):
        return "fake-response"

    def ping(self) -> bool:
        return True

    def list_models(self):
        return ["fake-model"]


class TestAgentFactory:
    def _make_factory(self, tmp_path: Path) -> tuple[AgentFactory, AgentRegistry]:
        registry = AgentRegistry()
        repo = MemoryRepository(tmp_path / "test.db")
        factory = AgentFactory(
            registry=registry,
            memory_repository=repo,
            work_dir=tmp_path,
        )
        return factory, registry

    def test_create_agent_registers_and_spawns(self, tmp_path: Path) -> None:
        factory, registry = self._make_factory(tmp_path)
        desc = AgentDescriptor(
            agent_id="new-1",
            name="NewAgent",
            role=AgentRole.EXECUTOR,
        )
        client = FakeClient()
        runtime = factory.create_agent(desc, client=client)
        assert runtime.agent_id == "new-1"
        assert "new-1" in registry
        agent = registry.get("new-1")
        assert agent is not None
        assert agent.state == AgentState.IDLE

    def test_create_from_existing(self, tmp_path: Path) -> None:
        factory, registry = self._make_factory(tmp_path)
        runtime = factory.create_from_existing(
            agent_id="polluks",
            name="Polluks",
            role=AgentRole.EXECUTOR,
            model_backend="ollama",
            model_name="test-model",
        )
        assert runtime.agent_id == "polluks"
        assert "polluks" in registry
        agent = registry.get("polluks")
        assert agent is not None
        assert agent.name == "Polluks"

    def test_create_agent_duplicate_raises(self, tmp_path: Path) -> None:
        factory, registry = self._make_factory(tmp_path)
        desc1 = AgentDescriptor(agent_id="dup", name="A1", role=AgentRole.EXECUTOR)
        desc2 = AgentDescriptor(agent_id="dup", name="A2", role=AgentRole.EXECUTOR)
        factory.create_agent(desc1)
        with pytest.raises(KeyError, match="already registered"):
            factory.create_agent(desc2)

    def test_generate_id_is_unique(self) -> None:
        ids = {AgentFactory.generate_id() for _ in range(100)}
        assert len(ids) == 100  # all unique

    def test_create_with_lifecycle_logger(self, tmp_path: Path) -> None:
        log_path = tmp_path / "lifecycle.jsonl"
        logger = LifecycleLogger(log_path)
        registry = AgentRegistry(lifecycle_logger=logger)
        repo = MemoryRepository(tmp_path / "test.db")
        factory = AgentFactory(
            registry=registry,
            memory_repository=repo,
            lifecycle_logger=logger,
        )
        desc = AgentDescriptor(agent_id="logged", name="Logged", role=AgentRole.EXECUTOR)
        factory.create_agent(desc)
        content = log_path.read_text()
        assert "agent.registered" in content
