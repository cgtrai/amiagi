"""Task domain model — priority, status, dependencies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TaskPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    @property
    def sort_key(self) -> int:
        return {
            TaskPriority.CRITICAL: 0,
            TaskPriority.HIGH: 1,
            TaskPriority.NORMAL: 2,
            TaskPriority.LOW: 3,
        }[self]


class TaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """A unit of work that can be assigned to an agent."""

    task_id: str
    title: str
    description: str = ""
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent_id: str | None = None
    parent_task_id: str | None = None
    dependencies: list[str] = field(default_factory=list)  # task_ids
    deadline: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def generate_id() -> str:
        return uuid.uuid4().hex[:12]

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def assign_to(self, agent_id: str) -> None:
        self.assigned_agent_id = agent_id
        self.status = TaskStatus.ASSIGNED

    def start(self) -> None:
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = datetime.now(timezone.utc)

    def complete(self, result: str = "") -> None:
        self.status = TaskStatus.DONE
        self.result = result
        self.completed_at = datetime.now(timezone.utc)

    def fail(self, reason: str = "") -> None:
        self.status = TaskStatus.FAILED
        self.result = reason
        self.completed_at = datetime.now(timezone.utc)

    def cancel(self) -> None:
        self.status = TaskStatus.CANCELLED
        self.completed_at = datetime.now(timezone.utc)
