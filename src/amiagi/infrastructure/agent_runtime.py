"""AgentRuntime — wrapper on ChatService + descriptor with lifecycle hooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from amiagi.application.chat_service import ChatService
from amiagi.application.supervisor_service import SupervisorService
from amiagi.domain.agent import AgentDescriptor, AgentState


# Lifecycle hook signature:  (agent_runtime) -> None
LifecycleHook = Callable[["AgentRuntime"], None]


@dataclass
class AgentRuntime:
    """Binds a :class:`ChatService` (or :class:`SupervisorService`) to an
    :class:`AgentDescriptor` and exposes start / pause / resume / terminate
    lifecycle operations with hook support.
    """

    descriptor: AgentDescriptor
    chat_service: ChatService | None = None
    supervisor_service: SupervisorService | None = None

    # lifecycle hooks
    on_spawn: list[LifecycleHook] = field(default_factory=list)
    on_pause: list[LifecycleHook] = field(default_factory=list)
    on_resume: list[LifecycleHook] = field(default_factory=list)
    on_terminate: list[LifecycleHook] = field(default_factory=list)
    on_error: list[LifecycleHook] = field(default_factory=list)

    # ---- public API ----

    @property
    def agent_id(self) -> str:
        return self.descriptor.agent_id

    @property
    def state(self) -> AgentState:
        return self.descriptor.state

    def ask(self, user_message: str, *, actor: str = "Sponsor") -> str:
        """Forward a question to the underlying ChatService."""
        if self.chat_service is None:
            raise RuntimeError(f"Agent {self.agent_id!r} has no chat_service")
        if self.descriptor.state not in (AgentState.IDLE, AgentState.WORKING):
            raise RuntimeError(
                f"Agent {self.agent_id!r} cannot answer in state {self.descriptor.state.value!r}"
            )
        self.descriptor.transition_to(AgentState.WORKING)
        try:
            result = self.chat_service.ask(user_message, actor=actor)
        except Exception:
            self.descriptor.transition_to(AgentState.ERROR)
            self._fire(self.on_error)
            raise
        self.descriptor.transition_to(AgentState.IDLE)
        return result

    def pause(self) -> None:
        self.descriptor.transition_to(AgentState.PAUSED)
        self._fire(self.on_pause)

    def resume(self) -> None:
        self.descriptor.transition_to(AgentState.IDLE)
        self._fire(self.on_resume)

    def terminate(self) -> None:
        self.descriptor.transition_to(AgentState.TERMINATED)
        self._fire(self.on_terminate)

    def spawn(self) -> None:
        """Called after creation to fire the on_spawn hooks."""
        self._fire(self.on_spawn)

    # ---- internals ----

    def _fire(self, hooks: list[LifecycleHook]) -> None:
        for hook in hooks:
            try:
                hook(self)
            except Exception:
                pass  # lifecycle hooks must not break the runtime
