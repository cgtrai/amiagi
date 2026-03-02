"""Phase 11 — Dynamic scaler (application).

Monitors team workload and recommends scale-up / scale-down
actions based on task queue depth and agent utilisation.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScaleEvent:
    """Record of a scaling action."""

    timestamp: float = field(default_factory=time.time)
    direction: str = ""  # "up" or "down"
    agent_role: str = ""
    reason: str = ""
    team_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "direction": self.direction,
            "agent_role": self.agent_role,
            "reason": self.reason,
            "team_id": self.team_id,
        }


class DynamicScaler:
    """Auto-scaling recommender for agent teams.

    When the pending-task count exceeds *scale_up_threshold* the scaler
    suggests adding a temporary agent.  When the count drops below
    *scale_down_threshold* it suggests retiring one.
    """

    def __init__(
        self,
        *,
        scale_up_threshold: int = 5,
        scale_down_threshold: int = 1,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self._scale_up_threshold = scale_up_threshold
        self._scale_down_threshold = scale_down_threshold
        self._cooldown_seconds = cooldown_seconds
        self._last_scale_time: float = 0.0
        self._events: list[ScaleEvent] = []
        self._lock = threading.Lock()

    # ---- thresholds ----

    @property
    def scale_up_threshold(self) -> int:
        return self._scale_up_threshold

    @scale_up_threshold.setter
    def scale_up_threshold(self, value: int) -> None:
        self._scale_up_threshold = max(1, value)

    @property
    def scale_down_threshold(self) -> int:
        return self._scale_down_threshold

    @scale_down_threshold.setter
    def scale_down_threshold(self, value: int) -> None:
        self._scale_down_threshold = max(0, value)

    # ---- evaluation ----

    def evaluate(
        self,
        *,
        pending_tasks: int,
        active_agents: int,
        team_id: str = "",
    ) -> ScaleEvent | None:
        """Evaluate whether scaling is needed.

        Returns a :class:`ScaleEvent` when a recommendation is made,
        or ``None`` if no action is needed.
        """
        now = time.time()
        with self._lock:
            if now - self._last_scale_time < self._cooldown_seconds:
                return None

            if pending_tasks >= self._scale_up_threshold:
                event = ScaleEvent(
                    timestamp=now,
                    direction="up",
                    agent_role="general",
                    reason=f"Pending tasks ({pending_tasks}) >= threshold ({self._scale_up_threshold})",
                    team_id=team_id,
                )
                self._events.append(event)
                self._last_scale_time = now
                return event

            if active_agents > 1 and pending_tasks <= self._scale_down_threshold:
                event = ScaleEvent(
                    timestamp=now,
                    direction="down",
                    agent_role="general",
                    reason=f"Pending tasks ({pending_tasks}) <= threshold ({self._scale_down_threshold}), agents={active_agents}",
                    team_id=team_id,
                )
                self._events.append(event)
                self._last_scale_time = now
                return event

            return None

    # ---- apply scaling ----

    def apply_scale(
        self,
        event: ScaleEvent,
        *,
        registry: Any = None,
        factory: Any = None,
        client: Any = None,
    ) -> str | None:
        """Actually spawn or terminate an agent based on a :class:`ScaleEvent`.

        Parameters
        ----------
        event:
            The scale event (``direction="up"`` or ``"down"``).
        registry:
            An :class:`AgentRegistry` instance.
        factory:
            An :class:`AgentFactory` instance (used for ``direction="up"``).
        client:
            An optional ``ChatCompletionClient`` to wire into new agents.

        Returns
        -------
        str | None
            The ``agent_id`` of the spawned or terminated agent, or ``None``
            if the operation was not applicable.
        """
        if registry is None:
            return None

        if event.direction == "up" and factory is not None:
            from amiagi.domain.agent import AgentDescriptor as FullDescriptor, AgentRole
            agent_id = factory.generate_id()
            role_str = event.agent_role or "general"
            descriptor = FullDescriptor(
                agent_id=agent_id,
                name=f"scaler-{role_str}-{agent_id[:6]}",
                role=AgentRole.EXECUTOR,
                metadata={"scaled": True, "team_id": event.team_id},
            )
            factory.create_agent(descriptor, client=client)
            return agent_id

        if event.direction == "down":
            from amiagi.domain.agent import AgentState
            # Find a non-essential agent that is idle and scaled
            candidates = [
                a for a in registry.list_all()
                if a.state == AgentState.IDLE
                and a.metadata.get("scaled", False)
            ]
            if not candidates:
                # Fallback: any idle agent
                candidates = [
                    a for a in registry.list_all()
                    if a.state == AgentState.IDLE
                ]
            if candidates:
                target = candidates[-1]
                target.transition_to(AgentState.TERMINATED)
                registry.unregister(target.agent_id)
                return target.agent_id

        return None

    # ---- history ----

    def history(self, limit: int = 20) -> list[ScaleEvent]:
        with self._lock:
            return list(reversed(self._events[-limit:]))

    def clear_history(self) -> None:
        with self._lock:
            self._events.clear()
            self._last_scale_time = 0.0

    # ---- status ----

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "scale_up_threshold": self._scale_up_threshold,
                "scale_down_threshold": self._scale_down_threshold,
                "cooldown_seconds": self._cooldown_seconds,
                "events_count": len(self._events),
            }
