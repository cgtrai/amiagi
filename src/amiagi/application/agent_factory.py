"""AgentFactory — creates AgentRuntime instances from descriptors."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.chat_service import ChatService
from amiagi.application.model_client_protocol import ChatCompletionClient
from amiagi.application.skills_loader import SkillsLoader
from amiagi.application.supervisor_service import SupervisorService
from amiagi.domain.agent import AgentDescriptor, AgentRole, AgentState
from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.agent_runtime import AgentRuntime
from amiagi.infrastructure.lifecycle_logger import LifecycleLogger
from amiagi.infrastructure.memory_repository import MemoryRepository


class AgentFactory:
    """Creates fully-wired :class:`AgentRuntime` instances and registers them.

    Typical usage::

        runtime = factory.create_agent(descriptor, client=my_client)
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        memory_repository: MemoryRepository,
        activity_logger: ActivityLogger | None = None,
        lifecycle_logger: LifecycleLogger | None = None,
        skills_loader: SkillsLoader | None = None,
        work_dir: Path = Path("./amiagi-my-work"),
    ) -> None:
        self._registry = registry
        self._memory_repository = memory_repository
        self._activity_logger = activity_logger
        self._lifecycle_logger = lifecycle_logger
        self._skills_loader = skills_loader
        self._work_dir = work_dir

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    @staticmethod
    def generate_id() -> str:
        """Create a short unique agent ID."""
        return uuid.uuid4().hex[:12]

    def create_agent(
        self,
        descriptor: AgentDescriptor,
        *,
        client: ChatCompletionClient | None = None,
        supervisor_service: SupervisorService | None = None,
    ) -> AgentRuntime:
        """Build a :class:`AgentRuntime`, register it and fire the spawn hook.

        Parameters
        ----------
        descriptor:
            Pre-filled agent descriptor.  ``agent_id`` must be unique.
        client:
            If given, a ``ChatService`` will be constructed using this client.
        supervisor_service:
            Injected supervisor (if the agent has one).
        """
        chat_service: ChatService | None = None
        if client is not None:
            chat_service = ChatService(
                memory_repository=self._memory_repository,
                ollama_client=client,
                activity_logger=self._activity_logger,
                work_dir=self._work_dir,
                supervisor_service=supervisor_service,
                skills_loader=self._skills_loader,
            )

        runtime = AgentRuntime(
            descriptor=descriptor,
            chat_service=chat_service,
            supervisor_service=supervisor_service,
        )

        self._registry.register(descriptor)
        runtime.spawn()
        return runtime

    def create_from_existing(
        self,
        *,
        agent_id: str,
        name: str,
        role: AgentRole,
        chat_service: ChatService | None = None,
        supervisor_service: SupervisorService | None = None,
        model_backend: str = "ollama",
        model_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AgentRuntime:
        """Wrap already-constructed services (e.g. legacy Polluks/Kastor)."""
        descriptor = AgentDescriptor(
            agent_id=agent_id,
            name=name,
            role=role,
            model_backend=model_backend,
            model_name=model_name,
            metadata=metadata or {},
        )
        runtime = AgentRuntime(
            descriptor=descriptor,
            chat_service=chat_service,
            supervisor_service=supervisor_service,
        )

        self._registry.register(descriptor)
        runtime.spawn()
        return runtime
