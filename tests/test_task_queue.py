"""Tests for Task domain model and TaskQueue."""

from __future__ import annotations

import threading

import pytest

from amiagi.application.task_queue import TaskQueue
from amiagi.domain.task import Task, TaskPriority, TaskStatus


def _make_task(
    task_id: str = "t1",
    title: str = "Test task",
    priority: TaskPriority = TaskPriority.NORMAL,
    dependencies: list[str] | None = None,
    **kwargs,
) -> Task:
    return Task(
        task_id=task_id,
        title=title,
        priority=priority,
        dependencies=dependencies or [],
        **kwargs,
    )


# ------------------------------------------------------------------
# Task domain model
# ------------------------------------------------------------------


class TestTaskDomain:
    def test_default_status_pending(self) -> None:
        t = _make_task()
        assert t.status == TaskStatus.PENDING

    def test_assign_to(self) -> None:
        t = _make_task()
        t.assign_to("agent-1")
        assert t.assigned_agent_id == "agent-1"
        assert t.status == TaskStatus.ASSIGNED

    def test_start(self) -> None:
        t = _make_task()
        t.start()
        assert t.status == TaskStatus.IN_PROGRESS
        assert t.started_at is not None

    def test_complete(self) -> None:
        t = _make_task()
        t.complete("done!")
        assert t.status == TaskStatus.DONE
        assert t.result == "done!"
        assert t.completed_at is not None

    def test_fail(self) -> None:
        t = _make_task()
        t.fail("error occurred")
        assert t.status == TaskStatus.FAILED
        assert t.result == "error occurred"

    def test_cancel(self) -> None:
        t = _make_task()
        t.cancel()
        assert t.status == TaskStatus.CANCELLED

    def test_is_terminal(self) -> None:
        for status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            t = _make_task()
            t.status = status
            assert t.is_terminal is True

    def test_is_not_terminal(self) -> None:
        for status in (TaskStatus.PENDING, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
            t = _make_task()
            t.status = status
            assert t.is_terminal is False

    def test_priority_sort_key(self) -> None:
        assert TaskPriority.CRITICAL.sort_key < TaskPriority.HIGH.sort_key
        assert TaskPriority.HIGH.sort_key < TaskPriority.NORMAL.sort_key
        assert TaskPriority.NORMAL.sort_key < TaskPriority.LOW.sort_key

    def test_generate_id(self) -> None:
        ids = {Task.generate_id() for _ in range(50)}
        assert len(ids) == 50  # all unique


# ------------------------------------------------------------------
# TaskQueue
# ------------------------------------------------------------------


class TestTaskQueue:
    def test_enqueue_and_get(self) -> None:
        q = TaskQueue()
        t = _make_task("t1")
        q.enqueue(t)
        assert q.get("t1") is t

    def test_enqueue_duplicate_raises(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        with pytest.raises(KeyError, match="already in queue"):
            q.enqueue(_make_task("t1"))

    def test_len(self) -> None:
        q = TaskQueue()
        assert len(q) == 0
        q.enqueue(_make_task("t1"))
        assert len(q) == 1

    def test_list_all(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        q.enqueue(_make_task("t2"))
        assert len(q.list_all()) == 2

    def test_list_by_status(self) -> None:
        q = TaskQueue()
        t1 = _make_task("t1")
        t2 = _make_task("t2")
        q.enqueue(t1)
        q.enqueue(t2)
        q.mark_done("t2", "ok")
        pending = q.list_by_status(TaskStatus.PENDING)
        done = q.list_by_status(TaskStatus.DONE)
        assert len(pending) == 1
        assert len(done) == 1

    def test_remove(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        removed = q.remove("t1")
        assert removed.task_id == "t1"
        assert len(q) == 0

    def test_mark_done(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        q.mark_done("t1", "result")
        assert q.get("t1").status == TaskStatus.DONE
        assert q.get("t1").result == "result"

    def test_mark_failed(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        q.mark_failed("t1", "oops")
        assert q.get("t1").status == TaskStatus.FAILED

    def test_cancel(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        q.cancel("t1")
        assert q.get("t1").status == TaskStatus.CANCELLED

    def test_pending_count(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        q.enqueue(_make_task("t2"))
        q.mark_done("t1")
        assert q.pending_count() == 1

    def test_stats(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        q.enqueue(_make_task("t2"))
        q.mark_done("t1")
        s = q.stats()
        assert s.get("pending") == 1
        assert s.get("done") == 1


class TestTaskQueueReadyTasks:
    def test_no_dependencies_all_ready(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        q.enqueue(_make_task("t2"))
        ready = q.get_ready_tasks()
        assert len(ready) == 2

    def test_dependency_blocks(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        q.enqueue(_make_task("t2", dependencies=["t1"]))
        ready = q.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t1"

    def test_dependency_unblocks_after_done(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("t1"))
        q.enqueue(_make_task("t2", dependencies=["t1"]))
        q.mark_done("t1")
        ready = q.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t2"

    def test_priority_ordering(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("low", priority=TaskPriority.LOW))
        q.enqueue(_make_task("crit", priority=TaskPriority.CRITICAL))
        q.enqueue(_make_task("high", priority=TaskPriority.HIGH))
        ready = q.get_ready_tasks()
        assert [t.task_id for t in ready] == ["crit", "high", "low"]

    def test_subtasks(self) -> None:
        q = TaskQueue()
        q.enqueue(_make_task("parent"))
        q.enqueue(_make_task("child1", parent_task_id="parent"))
        q.enqueue(_make_task("child2", parent_task_id="parent"))
        subs = q.list_subtasks("parent")
        assert len(subs) == 2


class TestTaskQueueConcurrency:
    def test_concurrent_enqueue(self) -> None:
        q = TaskQueue()
        errors: list[Exception] = []

        def enqueue_task(i: int) -> None:
            try:
                q.enqueue(_make_task(f"t-{i}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=enqueue_task, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(q) == 50
