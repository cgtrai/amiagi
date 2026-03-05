"""Task template repository — CRUD for reusable workflow templates.

Templates are stored as YAML in ``dbo.task_templates``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_BUILTIN_NAMES = frozenset({
    "Code Review Pipeline",
    "Documentation Sprint",
    "Bug Investigation",
    "Refactoring Plan",
})


@dataclass
class TaskTemplate:
    id: str
    name: str
    description: str
    yaml_content: str
    tags: list[str] = field(default_factory=list)
    author_id: str | None = None
    is_public: bool = False
    use_count: int = 0
    created_at: datetime | None = None

    @property
    def parsed(self) -> dict[str, Any]:
        """Parse YAML content into a dict.  Returns empty dict on failure."""
        try:
            return yaml.safe_load(self.yaml_content) or {}
        except yaml.YAMLError:
            return {}

    @property
    def parameters(self) -> list[dict[str, str]]:
        """Extract parameter definitions from parsed YAML."""
        return self.parsed.get("parameters", [])

    @property
    def steps(self) -> list[dict[str, str]]:
        """Extract step definitions from parsed YAML."""
        return self.parsed.get("steps", [])

    def render_steps(self, values: dict[str, str]) -> list[dict[str, str]]:
        """Render steps with provided parameter values."""
        rendered = []
        for step in self.steps:
            new_step = dict(step)
            prompt = new_step.get("prompt", "")
            try:
                new_step["prompt"] = prompt.format(**values)
            except (KeyError, IndexError, ValueError):
                pass
            rendered.append(new_step)
        return rendered

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "yaml_content": self.yaml_content,
            "tags": self.tags,
            "author_id": self.author_id,
            "is_public": self.is_public,
            "use_count": self.use_count,
            "parameters": self.parameters,
            "steps": self.steps,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def validate_yaml(content: str) -> tuple[bool, str]:
    """Validate YAML content.  Returns (ok, error_message)."""
    try:
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            return False, "YAML must be a mapping"
        if "name" not in parsed:
            return False, "Missing required key: name"
        if "steps" not in parsed or not isinstance(parsed.get("steps"), list):
            return False, "Missing or invalid 'steps' list"
        return True, ""
    except yaml.YAMLError as exc:
        return False, str(exc)


def _row_to_template(row) -> TaskTemplate:
    return TaskTemplate(
        id=str(row["id"]),
        name=row["name"],
        description=row.get("description", ""),
        yaml_content=row["yaml_content"],
        tags=list(row.get("tags") or []),
        author_id=str(row["author_id"]) if row.get("author_id") else None,
        is_public=row.get("is_public", False),
        use_count=row.get("use_count", 0),
        created_at=row.get("created_at"),
    )


class TaskTemplateRepository:
    """CRUD for task templates."""

    def __init__(self, pool: "asyncpg.Pool") -> None:
        self._pool = pool

    async def list_templates(
        self, *, public_only: bool = False, limit: int = 50,
    ) -> list[TaskTemplate]:
        cond = "WHERE is_public = true" if public_only else ""
        rows = await self._pool.fetch(
            f"SELECT * FROM dbo.task_templates {cond} ORDER BY use_count DESC, created_at DESC LIMIT $1",
            limit,
        )
        return [_row_to_template(r) for r in rows]

    async def get(self, template_id: str) -> TaskTemplate | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM dbo.task_templates WHERE id = $1::uuid", template_id,
        )
        return _row_to_template(row) if row else None

    async def create(
        self, name: str, yaml_content: str,
        description: str = "", tags: list[str] | None = None,
        author_id: str | None = None, is_public: bool = False,
    ) -> TaskTemplate:
        ok, err = validate_yaml(yaml_content)
        if not ok:
            raise ValueError(f"Invalid YAML: {err}")
        row = await self._pool.fetchrow(
            """
            INSERT INTO dbo.task_templates (name, description, yaml_content, tags, author_id, is_public)
            VALUES ($1, $2, $3, $4, $5::uuid, $6)
            RETURNING *
            """,
            name, description, yaml_content, tags or [], author_id, is_public,
        )
        return _row_to_template(row)

    async def delete(self, template_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM dbo.task_templates WHERE id = $1::uuid", template_id,
        )
        return result.endswith("1")

    async def increment_use_count(self, template_id: str) -> None:
        await self._pool.execute(
            "UPDATE dbo.task_templates SET use_count = use_count + 1 WHERE id = $1::uuid",
            template_id,
        )

    async def export_yaml(self, template_id: str) -> str | None:
        """Return raw YAML for download."""
        row = await self._pool.fetchrow(
            "SELECT yaml_content FROM dbo.task_templates WHERE id = $1::uuid", template_id,
        )
        return row["yaml_content"] if row else None
