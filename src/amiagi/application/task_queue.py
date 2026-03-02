"""Priority queue with dependency resolution for tasks."""

from __future__ import annotations

import threading
from typing import Any

from amiagi.domain.task import Task, TaskPriority, TaskStatus


class TaskQueue:
    """Thread-safe priority queue with dependency-aware dequeue.

    Tasks are stored in-memory.  ``get_ready_tasks()`` returns only tasks
    whose dependencies are all in DONE status.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    # ---- queries ----

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_all(self) -> list[Task]:
        with self._lock:
            return list(self._tasks.values())

    def list_by_status(self, status: TaskStatus) -> list[Task]:
        with self._lock:
            return [t for t in self._tasks.values() if t.status == status]

    def list_subtasks(self, parent_task_id: str) -> list[Task]:
        with self._lock:
            return [
                t for t in self._tasks.values()
                if t.parent_task_id == parent_task_id
            ]

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    def pending_count(self) -> int:
        with self._lock:
            return sum(
                1 for t in self._tasks.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED)
            )

    # ---- mutations ----

    def enqueue(self, task: Task) -> None:
        """Add a task (or re-add after edit). Raises ``KeyError`` on duplicate ID."""
        with self._lock:
            if task.task_id in self._tasks:
                raise KeyError(f"Task {task.task_id!r} already in queue")
            self._tasks[task.task_id] = task

    def remove(self, task_id: str) -> Task:
        with self._lock:
            return self._tasks.pop(task_id)

    def mark_done(self, task_id: str, result: str = "") -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id!r} not found")
            task.complete(result)

    def mark_failed(self, task_id: str, reason: str = "") -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id!r} not found")
            task.fail(reason)

    def cancel(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id!r} not found")
            task.cancel()

    # ---- scheduling queries ----

    def get_ready_tasks(self) -> list[Task]:
        """Return PENDING tasks whose dependencies are all DONE, sorted by priority."""
        with self._lock:
            done_ids = {
                tid for tid, t in self._tasks.items()
                if t.status == TaskStatus.DONE
            }
            ready = [
                t for t in self._tasks.values()
                if t.status == TaskStatus.PENDING
                and all(dep in done_ids for dep in t.dependencies)
            ]
            ready.sort(key=lambda t: (t.priority.sort_key, t.created_at))
            return ready

    def dequeue_next(self, agent_skills: set[str] | None = None) -> Task | None:
        """Pop the highest-priority ready task.

        If *agent_skills* is given, the task's ``metadata.get('required_skills')``
        (if present) must be a subset of the agent's skills.
        """
        ready = self.get_ready_tasks()
        for task in ready:
            required = set(task.metadata.get("required_skills", []))
            if agent_skills is not None and required and not required.issubset(agent_skills):
                continue
            with self._lock:
                # Double-check it's still pending
                if task.task_id in self._tasks and task.status == TaskStatus.PENDING:
                    return task
        return None

    # ---- stats ----

    def stats(self) -> dict[str, int]:
        """Return counts per status."""
        with self._lock:
            counts: dict[str, int] = {}
            for t in self._tasks.values():
                counts[t.status.value] = counts.get(t.status.value, 0) + 1
            return counts
