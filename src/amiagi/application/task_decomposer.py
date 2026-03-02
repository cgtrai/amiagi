"""TaskDecomposer — breaks complex tasks into subtasks using LLM."""

from __future__ import annotations

import json
import re
from typing import Any

from amiagi.application.model_client_protocol import ChatCompletionClient
from amiagi.domain.task import Task, TaskPriority

_DECOMPOSE_PROMPT = """\
You are a project management expert.  Break down the following task into
smaller, actionable subtasks.  Each subtask should be independently
executable by an AI agent.

Task: {title}
Description: {description}

Return a JSON array of subtasks (no markdown fences, no explanation):
[
  {{
    "title": "subtask title",
    "description": "what to do",
    "priority": "normal",
    "dependencies": []
  }}
]

Where "dependencies" is a list of 0-based indices of subtasks this one
depends on (e.g. [0] means it depends on the first subtask).
Priority: "critical", "high", "normal", or "low".
"""


class TaskDecomposer:
    """Decomposes a complex task into a list of linked subtasks."""

    def __init__(self, client: ChatCompletionClient | None = None) -> None:
        self._client = client

    def decompose(self, task: Task) -> list[Task]:
        """Return a list of subtasks linked to *task*.

        Uses the LLM when available, otherwise returns a single-item list.
        """
        if self._client is not None:
            return self._decompose_with_llm(task)
        return self._trivial_decompose(task)

    # ---- internals ----

    def _decompose_with_llm(self, parent: Task) -> list[Task]:
        assert self._client is not None
        prompt = _DECOMPOSE_PROMPT.format(
            title=parent.title,
            description=parent.description or "(no further description)",
        )
        try:
            raw = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a task decomposition expert. Return ONLY valid JSON.",
            )
            items = self._parse_json_array(raw)
            if not items:
                return self._trivial_decompose(parent)

            subtasks: list[Task] = []
            id_map: dict[int, str] = {}

            for idx, item in enumerate(items):
                tid = Task.generate_id()
                id_map[idx] = tid

                priority_str = item.get("priority", "normal").lower()
                try:
                    priority = TaskPriority(priority_str)
                except ValueError:
                    priority = TaskPriority.NORMAL

                subtasks.append(Task(
                    task_id=tid,
                    title=item.get("title", f"Subtask {idx + 1}"),
                    description=item.get("description", ""),
                    priority=priority,
                    parent_task_id=parent.task_id,
                ))

            # Resolve dependency indices → task IDs
            for idx, item in enumerate(items):
                dep_indices = item.get("dependencies", [])
                deps: list[str] = []
                for di in dep_indices:
                    if isinstance(di, int) and di in id_map:
                        deps.append(id_map[di])
                subtasks[idx].dependencies = deps

            return subtasks

        except Exception:
            return self._trivial_decompose(parent)

    @staticmethod
    def _trivial_decompose(parent: Task) -> list[Task]:
        """Fallback: wrap the parent as its own single subtask."""
        return [
            Task(
                task_id=Task.generate_id(),
                title=parent.title,
                description=parent.description,
                priority=parent.priority,
                parent_task_id=parent.task_id,
            )
        ]

    @staticmethod
    def _parse_json_array(text: str) -> list[dict[str, Any]]:
        cleaned = text.strip()
        if "```" in cleaned:
            parts = cleaned.split("```")
            for part in parts:
                stripped = part.strip()
                if stripped.startswith("json"):
                    stripped = stripped[4:].strip()
                if stripped.startswith("["):
                    cleaned = stripped
                    break
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, ValueError):
            match = re.search(r"\[.*\]", cleaned, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group())
                    if isinstance(result, list):
                        return result
                except (json.JSONDecodeError, ValueError):
                    pass
        return []
