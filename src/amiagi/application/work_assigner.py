"""WorkAssigner — matches ready tasks to idle agents."""

from __future__ import annotations

from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.task_queue import TaskQueue
from amiagi.domain.agent import AgentDescriptor, AgentState
from amiagi.domain.task import Task


class WorkAssigner:
    """Assigns ready tasks to idle agents using a skill-matching algorithm.

    Algorithm:
    1. Filter agents by state == IDLE
    2. For each ready task, find agents whose skills match
    3. Pick the least-loaded (currently: just the first idle match)
    4. Assign the task and update agent state
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        task_queue: TaskQueue,
    ) -> None:
        self._registry = registry
        self._task_queue = task_queue

    def assign_pending(self) -> list[tuple[Task, AgentDescriptor]]:
        """Match all ready tasks to idle agents.

        Returns a list of (task, agent) pairs that were assigned.
        """
        ready_tasks = self._task_queue.get_ready_tasks()
        idle_agents = self._registry.list_by_state(AgentState.IDLE)

        if not ready_tasks or not idle_agents:
            return []

        assignments: list[tuple[Task, AgentDescriptor]] = []
        used_agents: set[str] = set()

        for task in ready_tasks:
            required_skills = set(task.metadata.get("required_skills", []))

            best_agent: AgentDescriptor | None = None
            for agent in idle_agents:
                if agent.agent_id in used_agents:
                    continue
                # If task requires specific skills, check agent has them
                if required_skills:
                    agent_skills = set(agent.skills)
                    if not required_skills.issubset(agent_skills):
                        continue
                best_agent = agent
                break

            if best_agent is not None:
                task.assign_to(best_agent.agent_id)
                used_agents.add(best_agent.agent_id)
                assignments.append((task, best_agent))

        return assignments

    def assign_task_to_agent(self, task: Task, agent_id: str) -> bool:
        """Manually assign *task* to a specific agent.

        Returns True on success, False if agent not found or not idle.
        """
        agent = self._registry.get(agent_id)
        if agent is None or agent.state != AgentState.IDLE:
            return False
        task.assign_to(agent_id)
        return True
