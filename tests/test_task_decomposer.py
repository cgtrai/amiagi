"""Tests for TaskDecomposer (Phase 3)."""

from __future__ import annotations

from amiagi.application.task_decomposer import TaskDecomposer
from amiagi.domain.task import Task, TaskPriority


class TestTaskDecomposer:
    def test_trivial_decompose_without_client(self) -> None:
        decomposer = TaskDecomposer(client=None)
        parent = Task(task_id="p1", title="Big task", description="Do many things")
        subtasks = decomposer.decompose(parent)
        assert len(subtasks) == 1
        assert subtasks[0].title == "Big task"
        assert subtasks[0].parent_task_id == "p1"

    def test_trivial_decompose_preserves_priority(self) -> None:
        decomposer = TaskDecomposer(client=None)
        parent = Task(task_id="p1", title="Urgent", priority=TaskPriority.HIGH)
        subtasks = decomposer.decompose(parent)
        assert subtasks[0].priority == TaskPriority.HIGH

    def test_trivial_decompose_generates_unique_id(self) -> None:
        decomposer = TaskDecomposer(client=None)
        parent = Task(task_id="p1", title="Task")
        subtasks = decomposer.decompose(parent)
        assert subtasks[0].task_id != "p1"

    def test_decompose_with_mock_llm(self) -> None:
        """Mock LLM returns valid JSON array of subtasks."""
        import json

        sub_items = [
            {"title": "Setup environment", "description": "Install deps", "priority": "high", "dependencies": []},
            {"title": "Write code", "description": "Implement feature", "priority": "normal", "dependencies": [0]},
            {"title": "Write tests", "description": "Add unit tests", "priority": "normal", "dependencies": [1]},
        ]

        class MockClient:
            def chat(self, *, messages, system_prompt=""):
                return json.dumps(sub_items)

        decomposer = TaskDecomposer(client=MockClient())
        parent = Task(task_id="p1", title="Build feature X", description="Full implementation")
        subtasks = decomposer.decompose(parent)
        assert len(subtasks) == 3
        assert subtasks[0].title == "Setup environment"
        assert subtasks[0].priority == TaskPriority.HIGH
        assert subtasks[0].parent_task_id == "p1"
        # Second subtask should depend on first
        assert len(subtasks[1].dependencies) == 1
        assert subtasks[1].dependencies[0] == subtasks[0].task_id

    def test_decompose_with_llm_returning_garbage_falls_back(self) -> None:
        """When LLM returns non-JSON, fall back to trivial decompose."""

        class MockClient:
            def chat(self, *, messages, system_prompt=""):
                return "I'm not sure how to decompose this task."

        decomposer = TaskDecomposer(client=MockClient())
        parent = Task(task_id="p1", title="Ambiguous task")
        subtasks = decomposer.decompose(parent)
        # Should fall back to trivial (single subtask)
        assert len(subtasks) == 1
        assert subtasks[0].parent_task_id == "p1"

    def test_decompose_with_llm_raising_falls_back(self) -> None:
        """When LLM raises, fall back to trivial decompose."""

        class MockClient:
            def chat(self, *, messages, system_prompt=""):
                raise RuntimeError("Connection refused")

        decomposer = TaskDecomposer(client=MockClient())
        parent = Task(task_id="p1", title="Task")
        subtasks = decomposer.decompose(parent)
        assert len(subtasks) == 1

    def test_parse_json_array_clean(self) -> None:
        result = TaskDecomposer._parse_json_array('[{"title": "a"}, {"title": "b"}]')
        assert len(result) == 2

    def test_parse_json_array_with_markdown_fences(self) -> None:
        text = '```json\n[{"title": "a"}]\n```'
        result = TaskDecomposer._parse_json_array(text)
        assert len(result) == 1

    def test_parse_json_array_with_surrounding_text(self) -> None:
        text = 'Here are the subtasks:\n[{"title": "a"}]\nDone.'
        result = TaskDecomposer._parse_json_array(text)
        assert len(result) == 1

    def test_parse_json_array_empty(self) -> None:
        result = TaskDecomposer._parse_json_array("no json here")
        assert result == []
