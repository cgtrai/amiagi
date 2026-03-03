"""Thread-safe agent registry with lifecycle event logging."""

from __future__ import annotations

import threading
from typing import Any

from amiagi.domain.agent import AgentDescriptor, AgentRole, AgentState
from amiagi.infrastructure.lifecycle_logger import LifecycleLogger


class AgentRegistry:
    """Central repository of all live agents.

    Thread-safe: every mutating operation is guarded by ``_lock``.
    Every registration / state change is emitted to JSONL via *lifecycle_logger*.
    """

    def __init__(self, lifecycle_logger: LifecycleLogger | None = None) -> None:
        self._agents: dict[str, AgentDescriptor] = {}
        self._lock = threading.Lock()
        self._lifecycle_logger = lifecycle_logger

    # ---- queries (read-only, still lock for snapshot consistency) ----

    def get(self, agent_id: str) -> AgentDescriptor | None:
        with self._lock:
            return self._agents.get(agent_id)

    def list_all(self) -> list[AgentDescriptor]:
        with self._lock:
            return list(self._agents.values())

    def list_by_role(self, role: AgentRole) -> list[AgentDescriptor]:
        with self._lock:
            return [a for a in self._agents.values() if a.role == role]

    def list_by_state(self, state: AgentState) -> list[AgentDescriptor]:
        with self._lock:
            return [a for a in self._agents.values() if a.state == state]

    def list_active(self) -> list[AgentDescriptor]:
        """Return agents that are IDLE or WORKING (non-terminated, non-paused)."""
        with self._lock:
            return [
                a for a in self._agents.values()
                if a.state in (AgentState.IDLE, AgentState.WORKING)
            ]

    def __len__(self) -> int:
        with self._lock:
            return len(self._agents)

    def __contains__(self, agent_id: str) -> bool:
        with self._lock:
            return agent_id in self._agents

    # ---- mutations ----

    def register(self, descriptor: AgentDescriptor) -> None:
        """Add a new agent to the registry. Raises ``KeyError`` on duplicates."""
        with self._lock:
            if descriptor.agent_id in self._agents:
                raise KeyError(
                    f"Agent {descriptor.agent_id!r} already registered"
                )
            self._agents[descriptor.agent_id] = descriptor
        self._emit("agent.registered", descriptor.agent_id, {
            "name": descriptor.name,
            "role": descriptor.role.value,
            "model_backend": descriptor.model_backend,
            "model_name": descriptor.model_name,
        })

    def unregister(self, agent_id: str) -> AgentDescriptor:
        """Remove and return the agent. Raises ``KeyError`` if absent."""
        with self._lock:
            descriptor = self._agents.pop(agent_id)
        self._emit("agent.unregistered", agent_id)
        return descriptor

    def update_state(
        self,
        agent_id: str,
        new_state: AgentState,
        *,
        reason: str = "",
    ) -> None:
        """Transition an agent to *new_state* (validated)."""
        with self._lock:
            descriptor = self._agents.get(agent_id)
            if descriptor is None:
                raise KeyError(f"Agent {agent_id!r} not found")
            old_state = descriptor.state
            descriptor.transition_to(new_state)  # validates
        self._emit("agent.state_changed", agent_id, {
            "old_state": old_state.value,
            "new_state": new_state.value,
            "reason": reason,
        })

    def update_model(
        self,
        agent_id: str,
        model_name: str,
        model_backend: str = "",
    ) -> None:
        """Update the declared model for *agent_id*.

        Only changes fields that are non-empty so callers can update just one.
        """
        with self._lock:
            descriptor = self._agents.get(agent_id)
            if descriptor is None:
                raise KeyError(f"Agent {agent_id!r} not found")
            if model_name:
                descriptor.model_name = model_name
            if model_backend:
                descriptor.model_backend = model_backend
        self._emit("agent.model_changed", agent_id, {
            "model_name": model_name or descriptor.model_name,
            "model_backend": model_backend or descriptor.model_backend,
        })

    # ---- helpers ----

    def _emit(
        self,
        event: str,
        agent_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._lifecycle_logger is not None:
            self._lifecycle_logger.log(
                agent_id=agent_id,
                event=event,
                details=details,
            )
