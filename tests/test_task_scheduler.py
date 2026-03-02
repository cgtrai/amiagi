"""Tests for TaskScheduler (Phase 3)."""

from __future__ import annotations

import time
from typing import Any

from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.task_queue import TaskQueue
from amiagi.application.work_assigner import WorkAssigner
from amiagi.domain.agent import AgentDescriptor, AgentRole, AgentState
from amiagi.domain.task import Task, TaskPriority, TaskStatus
from amiagi.infrastructure.task_scheduler import TaskScheduler


def _make_queue_with_task(task_id: str = "t1", title: str = "Test task") -> TaskQueue:
    q = TaskQueue()
    q.enqueue(Task(task_id=task_id, title=title))
    return q


def _make_registry_with_agent(agent_id: str = "a1") -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(AgentDescriptor(
        agent_id=agent_id,
        name="Agent A",
        role=AgentRole.EXECUTOR,
        state=AgentState.IDLE,
    ))
    return reg


class TestTaskScheduler:
    def test_initial_state(self) -> None:
        q = TaskQueue()
        reg = AgentRegistry()
        assigner = WorkAssigner(registry=reg, task_queue=q)
        sched = TaskScheduler(task_queue=q, work_assigner=assigner, registry=reg)
        assert sched.running is False

    def test_start_stop(self) -> None:
        q = TaskQueue()
        reg = AgentRegistry()
        assigner = WorkAssigner(registry=reg, task_queue=q)
        sched = TaskScheduler(
            task_queue=q,
            work_assigner=assigner,
            registry=reg,
            interval_seconds=0.1,
        )
        sched.start()
        assert sched.running is True
        time.sleep(0.15)
        sched.stop()
        assert sched.running is False

    def test_double_start_is_noop(self) -> None:
        q = TaskQueue()
        reg = AgentRegistry()
        assigner = WorkAssigner(registry=reg, task_queue=q)
        sched = TaskScheduler(task_queue=q, work_assigner=assigner, registry=reg)
        sched.start()
        sched.start()  # should be a no-op
        assert sched.running is True
        sched.stop()

    def test_tick_returns_zero_when_nothing_to_assign(self) -> None:
        q = TaskQueue()
        reg = AgentRegistry()
        assigner = WorkAssigner(registry=reg, task_queue=q)
        sched = TaskScheduler(task_queue=q, work_assigner=assigner, registry=reg)
        result = sched.tick()
        assert result == 0

    def test_tick_assigns_task_to_idle_agent(self) -> None:
        q = _make_queue_with_task("t1", "Build feature")
        reg = _make_registry_with_agent("a1")
        assigner = WorkAssigner(registry=reg, task_queue=q)
        sched = TaskScheduler(task_queue=q, work_assigner=assigner, registry=reg)
        count = sched.tick()
        assert count == 1
        task = q.get("t1")
        assert task is not None
        assert task.status == TaskStatus.ASSIGNED
        assert task.assigned_agent_id == "a1"

    def test_register_runtime(self) -> None:
        q = TaskQueue()
        reg = AgentRegistry()
        assigner = WorkAssigner(registry=reg, task_queue=q)
        sched = TaskScheduler(task_queue=q, work_assigner=assigner, registry=reg)
        # Just verify it doesn't crash — we'd need a proper AgentRuntime mock for exec
        assert sched._runtimes == {}

    def test_escalate_deadlines(self) -> None:
        """Verify that tasks approaching deadline get escalated to CRITICAL."""
        import datetime as _dt

        q = TaskQueue()
        # Create a task with a deadline 60 seconds from now (< 300s threshold)
        near_deadline = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=60)
        task = Task(task_id="t1", title="Urgent", priority=TaskPriority.NORMAL, deadline=near_deadline)
        q.enqueue(task)

        reg = AgentRegistry()
        assigner = WorkAssigner(registry=reg, task_queue=q)
        sched = TaskScheduler(task_queue=q, work_assigner=assigner, registry=reg)
        sched._escalate_deadlines()

        assert task.priority == TaskPriority.CRITICAL

    def test_no_escalation_for_far_deadline(self) -> None:
        import datetime as _dt

        q = TaskQueue()
        far_deadline = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)
        task = Task(task_id="t1", title="Not urgent", priority=TaskPriority.LOW, deadline=far_deadline)
        q.enqueue(task)

        reg = AgentRegistry()
        assigner = WorkAssigner(registry=reg, task_queue=q)
        sched = TaskScheduler(task_queue=q, work_assigner=assigner, registry=reg)
        sched._escalate_deadlines()

        assert task.priority == TaskPriority.LOW

    def test_no_escalation_for_terminal_task(self) -> None:
        import datetime as _dt

        q = TaskQueue()
        near_deadline = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=60)
        task = Task(task_id="t1", title="Done task", priority=TaskPriority.NORMAL, deadline=near_deadline)
        task.complete("finished")
        q.enqueue(task)

        reg = AgentRegistry()
        assigner = WorkAssigner(registry=reg, task_queue=q)
        sched = TaskScheduler(task_queue=q, work_assigner=assigner, registry=reg)
        sched._escalate_deadlines()

        # Terminal tasks should not be escalated
        assert task.priority == TaskPriority.NORMAL
