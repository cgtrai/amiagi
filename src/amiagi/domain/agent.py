"""Agent domain models — descriptors, state machine, roles."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar


class AgentState(str, Enum):
    """Lifecycle states for an agent."""

    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    ERROR = "error"
    TERMINATED = "terminated"


class AgentRole(str, Enum):
    """Primary role of the agent within the framework."""

    EXECUTOR = "executor"
    SUPERVISOR = "supervisor"
    SPECIALIST = "specialist"


@dataclass
class AgentDescriptor:
    """Full description of a registered agent.

    Mutable: ``state`` is updated throughout the lifecycle.
    Immutable fields (identity, configuration) should not change after
    registration.
    """

    agent_id: str
    name: str
    role: AgentRole
    persona_prompt: str = ""
    model_backend: str = "ollama"  # "ollama" | "openai"
    model_name: str = ""
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    state: AgentState = AgentState.IDLE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---- state transitions with validation ----

    _VALID_TRANSITIONS: ClassVar[dict[AgentState, frozenset[AgentState]]] = {
        AgentState.IDLE: frozenset({AgentState.WORKING, AgentState.PAUSED, AgentState.TERMINATED}),
        AgentState.WORKING: frozenset({AgentState.IDLE, AgentState.PAUSED, AgentState.ERROR, AgentState.TERMINATED}),
        AgentState.PAUSED: frozenset({AgentState.IDLE, AgentState.TERMINATED}),
        AgentState.ERROR: frozenset({AgentState.IDLE, AgentState.TERMINATED}),
        AgentState.TERMINATED: frozenset(),
    }

    def can_transition_to(self, new_state: AgentState) -> bool:
        return new_state in self._VALID_TRANSITIONS.get(self.state, frozenset())

    def transition_to(self, new_state: AgentState) -> None:
        """Transition to *new_state*, raising ``ValueError`` on illegal moves."""
        if not self.can_transition_to(new_state):
            raise ValueError(
                f"Agent {self.agent_id!r}: cannot transition "
                f"{self.state.value!r} → {new_state.value!r}"
            )
        self.state = new_state
