"""Tests for RouterTaskBridge — sponsor message → TaskDecomposer → TaskQueue."""

from __future__ import annotations

from amiagi.application.router_task_bridge import RouterTaskBridge
from amiagi.application.task_decomposer import TaskDecomposer
from amiagi.application.task_queue import TaskQueue
from amiagi.domain.task import Task, TaskStatus


class TestRouterTaskBridge:
    """Verify that sponsor messages are decomposed and enqueued."""

    def test_single_message_creates_root_and_subtask(self) -> None:
        tq = TaskQueue()
        # No LLM → trivial decomposer returns 1 subtask
        bridge = RouterTaskBridge(task_queue=tq, decomposer=TaskDecomposer())

        tasks = bridge.on_sponsor_message("Implement OAuth2 login flow")

        # root + 1 subtask from trivial decompose
        assert len(tasks) == 2
        root = tasks[0]
        assert root.title == "Implement OAuth2 login flow"
        assert root.description == "Implement OAuth2 login flow"

        sub = tasks[1]
        assert sub.parent_task_id == root.task_id

        # All enqueued
        assert len(tq) == 2

    def test_root_task_is_pending(self) -> None:
        tq = TaskQueue()
        bridge = RouterTaskBridge(task_queue=tq)
        tasks = bridge.on_sponsor_message("Fix the bug in parser")
        assert tasks[0].status == TaskStatus.PENDING

    def test_title_truncation(self) -> None:
        tq = TaskQueue()
        bridge = RouterTaskBridge(task_queue=tq)
        long_msg = "A" * 100
        tasks = bridge.on_sponsor_message(long_msg)
        assert len(tasks[0].title) <= 80

    def test_multiline_uses_first_line_as_title(self) -> None:
        tq = TaskQueue()
        bridge = RouterTaskBridge(task_queue=tq)
        msg = "Short title here\nMore details on second line\nAnd a third"
        tasks = bridge.on_sponsor_message(msg)
        assert tasks[0].title == "Short title here"
        assert tasks[0].description == msg

    def test_bridge_without_explicit_decomposer(self) -> None:
        tq = TaskQueue()
        bridge = RouterTaskBridge(task_queue=tq)
        tasks = bridge.on_sponsor_message("Do something")
        assert len(tasks) >= 2  # root + at least 1 subtask
        assert all(isinstance(t, Task) for t in tasks)

    def test_subtasks_linked_to_root(self) -> None:
        tq = TaskQueue()
        bridge = RouterTaskBridge(task_queue=tq, decomposer=TaskDecomposer())
        tasks = bridge.on_sponsor_message("Research market trends")
        root = tasks[0]
        for sub in tasks[1:]:
            assert sub.parent_task_id == root.task_id

    def test_all_tasks_in_queue(self) -> None:
        tq = TaskQueue()
        bridge = RouterTaskBridge(task_queue=tq)
        tasks = bridge.on_sponsor_message("Write unit tests")
        for t in tasks:
            queued = tq.get(t.task_id)
            assert queued is not None
            assert queued.task_id == t.task_id
