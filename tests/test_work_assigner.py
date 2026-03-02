"""Tests for WorkAssigner — skill-matching task-to-agent assignment."""

from __future__ import annotations

from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.task_queue import TaskQueue
from amiagi.application.work_assigner import WorkAssigner
from amiagi.domain.agent import AgentDescriptor, AgentRole, AgentState
from amiagi.domain.task import Task, TaskPriority


def _make_agent(agent_id: str, skills: list[str] | None = None) -> AgentDescriptor:
    return AgentDescriptor(
        agent_id=agent_id,
        name=f"Agent-{agent_id}",
        role=AgentRole.EXECUTOR,
        skills=skills or [],
    )


def _make_task(
    task_id: str,
    required_skills: list[str] | None = None,
    priority: TaskPriority = TaskPriority.NORMAL,
) -> Task:
    return Task(
        task_id=task_id,
        title=f"Task-{task_id}",
        priority=priority,
        metadata={"required_skills": required_skills or []},
    )


class TestWorkAssigner:
    def _setup(self) -> tuple[AgentRegistry, TaskQueue, WorkAssigner]:
        registry = AgentRegistry()
        queue = TaskQueue()
        assigner = WorkAssigner(registry=registry, task_queue=queue)
        return registry, queue, assigner

    def test_assign_simple(self) -> None:
        registry, queue, assigner = self._setup()
        registry.register(_make_agent("a1"))
        queue.enqueue(_make_task("t1"))
        assignments = assigner.assign_pending()
        assert len(assignments) == 1
        task, agent = assignments[0]
        assert task.task_id == "t1"
        assert agent.agent_id == "a1"

    def test_no_idle_agents(self) -> None:
        registry, queue, assigner = self._setup()
        agent = _make_agent("a1")
        registry.register(agent)
        registry.update_state("a1", AgentState.WORKING)
        queue.enqueue(_make_task("t1"))
        assert assigner.assign_pending() == []

    def test_no_ready_tasks(self) -> None:
        registry, queue, assigner = self._setup()
        registry.register(_make_agent("a1"))
        assert assigner.assign_pending() == []

    def test_skill_matching(self) -> None:
        registry, queue, assigner = self._setup()
        registry.register(_make_agent("python-dev", skills=["python"]))
        registry.register(_make_agent("js-dev", skills=["javascript"]))
        queue.enqueue(_make_task("py-task", required_skills=["python"]))
        assignments = assigner.assign_pending()
        assert len(assignments) == 1
        _, agent = assignments[0]
        assert agent.agent_id == "python-dev"

    def test_agent_used_once_per_tick(self) -> None:
        """Each agent is assigned at most one task per tick."""
        registry, queue, assigner = self._setup()
        registry.register(_make_agent("a1"))
        queue.enqueue(_make_task("t1"))
        queue.enqueue(_make_task("t2"))
        assignments = assigner.assign_pending()
        # Only one agent, so max 1 assignment
        assert len(assignments) == 1

    def test_assign_task_to_specific_agent(self) -> None:
        registry, queue, assigner = self._setup()
        registry.register(_make_agent("a1"))
        task = _make_task("t1")
        queue.enqueue(task)
        assigner.assign_task_to_agent(task, "a1")
        assert task.assigned_agent_id == "a1"
