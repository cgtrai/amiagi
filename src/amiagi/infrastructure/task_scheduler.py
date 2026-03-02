"""TaskScheduler — periodic loop that assigns ready tasks and drives execution."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable

from amiagi.application.agent_registry import AgentRegistry
from amiagi.application.task_queue import TaskQueue
from amiagi.application.work_assigner import WorkAssigner
from amiagi.domain.agent import AgentState
from amiagi.domain.task import Task, TaskPriority, TaskStatus
from amiagi.infrastructure.agent_runtime import AgentRuntime

logger = logging.getLogger(__name__)


class TaskScheduler:
    """Drives the task→agent assignment loop in a background thread.

    Every *interval_seconds* it:
    1. Calls ``WorkAssigner.assign_pending()``
    2. For each assignment, launches the agent in its own thread
    3. Escalates tasks approaching their deadline

    The scheduler is **non-blocking**: call ``start()`` and it runs in the
    background, ``stop()`` to shut it down.
    """

    def __init__(
        self,
        *,
        task_queue: TaskQueue,
        work_assigner: WorkAssigner,
        registry: AgentRegistry,
        runtimes: dict[str, AgentRuntime] | None = None,
        interval_seconds: float = 5.0,
        on_task_complete: Callable[[Task], None] | None = None,
        on_task_failed: Callable[[Task, Exception], None] | None = None,
    ) -> None:
        self._queue = task_queue
        self._assigner = work_assigner
        self._registry = registry
        self._runtimes = runtimes if runtimes is not None else {}
        self._interval = interval_seconds
        self._on_task_complete = on_task_complete
        self._on_task_failed = on_task_failed
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        return self._running

    def register_runtime(self, agent_id: str, runtime: AgentRuntime) -> None:
        self._runtimes[agent_id] = runtime

    def start(self) -> None:
        """Start the scheduler loop in a daemon thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="task-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the scheduler loop (non-blocking)."""
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)
            self._thread = None

    def tick(self) -> int:
        """Run one scheduling cycle synchronously. Returns number of assignments."""
        self._escalate_deadlines()
        assignments = self._assigner.assign_pending()
        for task, agent_desc in assignments:
            runtime = self._runtimes.get(agent_desc.agent_id)
            if runtime is not None:
                self._execute_task(task, runtime)
        return len(assignments)

    # ---- internals ----

    def _loop(self) -> None:
        while self._running:
            try:
                self.tick()
            except Exception:
                logger.exception("TaskScheduler tick failed")
            self._stop_event.wait(timeout=self._interval)

    def _execute_task(self, task: Task, runtime: AgentRuntime) -> None:
        """Run the task in a background thread."""
        def _run() -> None:
            try:
                task.start()
                result = runtime.ask(
                    f"Task: {task.title}\n\n{task.description}",
                    actor="TaskScheduler",
                )
                self._queue.mark_done(task.task_id, result)
                if self._on_task_complete:
                    self._on_task_complete(task)
            except Exception as exc:
                self._queue.mark_failed(task.task_id, str(exc))
                if self._on_task_failed:
                    self._on_task_failed(task, exc)

        thread = threading.Thread(
            target=_run,
            name=f"task-{task.task_id}",
            daemon=True,
        )
        thread.start()

    def _escalate_deadlines(self) -> None:
        """Escalate tasks approaching their deadline to CRITICAL priority."""
        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc)
        for task in self._queue.list_all():
            if task.is_terminal or task.deadline is None:
                continue
            remaining = (task.deadline - now).total_seconds()
            if remaining < 300 and task.priority != TaskPriority.CRITICAL:
                task.priority = TaskPriority.CRITICAL
