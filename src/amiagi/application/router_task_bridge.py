"""RouterTaskBridge — connects sponsor messages to the TaskQueue via TaskDecomposer.

ROADMAP 3.7: "Obecny router dla Sponsora staje się 'human task interface' —
wiadomość Sponsora → TaskDecomposer → subtaski → TaskQueue."

Usage::

    bridge = RouterTaskBridge(task_queue=tq, decomposer=td)
    tasks = bridge.on_sponsor_message("Implement OAuth2 login")
    # tasks are decomposed and enqueued automatically
"""

from __future__ import annotations

from typing import Any

from amiagi.application.task_decomposer import TaskDecomposer
from amiagi.application.task_queue import TaskQueue
from amiagi.domain.task import Task, TaskPriority


class RouterTaskBridge:
    """Translates incoming sponsor messages into tasks in the :class:`TaskQueue`.

    The bridge creates a root :class:`Task` from the message text, passes it
    through :class:`TaskDecomposer` to obtain subtasks, then enqueues all of
    them into the :class:`TaskQueue`.
    """

    def __init__(
        self,
        *,
        task_queue: TaskQueue,
        decomposer: TaskDecomposer | None = None,
    ) -> None:
        self._task_queue = task_queue
        self._decomposer = decomposer or TaskDecomposer()

    def on_sponsor_message(self, text: str) -> list[Task]:
        """Process a sponsor message: create root task, decompose, enqueue.

        Returns the list of tasks that were enqueued (root + subtasks).
        """
        root = Task(
            task_id=Task.generate_id(),
            title=self._extract_title(text),
            description=text,
            priority=TaskPriority.NORMAL,
        )
        self._task_queue.enqueue(root)

        subtasks = self._decomposer.decompose(root)
        for st in subtasks:
            self._task_queue.enqueue(st)

        return [root, *subtasks]

    @staticmethod
    def _extract_title(text: str) -> str:
        """Derive a short title from the first line of the message."""
        first_line = text.strip().split("\n")[0]
        if len(first_line) > 80:
            return first_line[:77] + "..."
        return first_line
