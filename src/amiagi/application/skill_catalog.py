"""Phase 11 — Skill catalog (application).

Centralised registry of known agent skills with metadata for
automatic matching.  Optionally delegates CRUD to
:class:`~amiagi.interfaces.web.skills.skill_repository.SkillRepository`
when a ``db_pool`` (asyncpg pool) is provided.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class SkillEntry:
    """Describes a single agent skill."""

    name: str
    description: str = ""
    required_tools: list[str] = field(default_factory=list)
    min_context_tokens: int = 0
    compatible_models: list[str] = field(default_factory=list)
    difficulty_level: str = "medium"  # easy / medium / hard
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "required_tools": self.required_tools,
            "min_context_tokens": self.min_context_tokens,
            "compatible_models": self.compatible_models,
            "difficulty_level": self.difficulty_level,
            "tags": self.tags,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillEntry":
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            required_tools=d.get("required_tools", []),
            min_context_tokens=d.get("min_context_tokens", 0),
            compatible_models=d.get("compatible_models", []),
            difficulty_level=d.get("difficulty_level", "medium"),
            tags=d.get("tags", []),
            metadata=d.get("metadata", {}),
        )


class SkillCatalog:
    """Thread-safe central skill registry.

    When *db_pool* is provided, CRUD operations are mirrored to
    PostgreSQL via :class:`SkillRepository`.  The in-memory dict
    remains the primary read path for low-latency access; the DB
    is a persistence layer that is hydrated at startup via
    :meth:`sync_from_db`.
    """

    def __init__(self, db_pool: "asyncpg.Pool | None" = None) -> None:
        self._skills: dict[str, SkillEntry] = {}
        self._lock = threading.Lock()
        self._db_pool = db_pool
        self._repo: Any | None = None
        if db_pool is not None:
            from amiagi.interfaces.web.skills.skill_repository import SkillRepository
            self._repo = SkillRepository(db_pool)

    # ---- DB sync helpers ----

    async def sync_from_db(self) -> int:
        """Load all active skills from PG into in-memory dict.

        Returns count of loaded skills.  No-op when no pool configured.
        """
        if self._repo is None:
            return 0
        records = await self._repo.list_skills(active_only=True)
        count = 0
        with self._lock:
            for rec in records:
                entry = SkillEntry(
                    name=rec.name,
                    description=rec.description,
                    required_tools=rec.compatible_tools,
                    tags=rec.trigger_keywords,
                    metadata={"db_id": rec.id, "category": rec.category},
                )
                self._skills[entry.name] = entry
                count += 1
        logger.info("SkillCatalog: synced %d skills from DB", count)
        return count

    # ---- CRUD ----

    def register(self, skill: SkillEntry) -> None:
        with self._lock:
            self._skills[skill.name] = skill
        if self._repo is not None:
            self._fire_and_forget(self._persist_register(skill))

    def unregister(self, name: str) -> bool:
        with self._lock:
            entry = self._skills.pop(name, None)
        if entry is not None and self._repo is not None:
            db_id = (entry.metadata or {}).get("db_id")
            if db_id:
                self._fire_and_forget(self._repo.delete_skill(db_id))
        return entry is not None

    async def _persist_register(self, skill: SkillEntry) -> None:
        """Upsert skill to PG — called in fire-and-forget mode."""
        repo = self._repo
        if repo is None:
            return
        try:
            existing = await repo.list_skills(active_only=False)
            for rec in existing:
                if rec.name == skill.name:
                    await repo.update_skill(
                        rec.id,
                        description=skill.description,
                        compatible_tools=skill.required_tools,
                        trigger_keywords=skill.tags,
                    )
                    return
            await repo.create_skill(
                name=skill.name,
                content=skill.description,
                description=skill.description,
                trigger_keywords=skill.tags,
                compatible_tools=skill.required_tools,
            )
        except Exception:
            logger.warning("SkillCatalog: failed to persist skill %s to DB", skill.name, exc_info=True)

    @staticmethod
    def _fire_and_forget(coro: Any) -> None:
        """Schedule a coroutine if an event loop is running."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            pass  # no event loop — skip DB write

    def get(self, name: str) -> SkillEntry | None:
        with self._lock:
            return self._skills.get(name)

    def list_skills(self) -> list[SkillEntry]:
        with self._lock:
            return list(self._skills.values())

    # ---- search / match ----

    def search(self, query: str) -> list[SkillEntry]:
        """Case-insensitive search across name, description and tags."""
        q = query.lower()
        with self._lock:
            return [
                s for s in self._skills.values()
                if q in s.name.lower()
                or q in s.description.lower()
                or any(q in t.lower() for t in s.tags)
            ]

    def match_for_tools(self, available_tools: list[str]) -> list[SkillEntry]:
        """Return skills whose required_tools are all in *available_tools*."""
        tool_set = set(available_tools)
        with self._lock:
            return [
                s for s in self._skills.values()
                if set(s.required_tools).issubset(tool_set)
            ]

    def match_for_model(self, model_name: str) -> list[SkillEntry]:
        with self._lock:
            return [
                s for s in self._skills.values()
                if not s.compatible_models or model_name in s.compatible_models
            ]

    # ---- bulk operations ----

    def load_json(self, path: Path) -> int:
        """Load skills from a JSON file (array of skill dicts)."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        count = 0
        for item in raw:
            skill = SkillEntry.from_dict(item)
            self.register(skill)
            count += 1
        return count

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = [s.to_dict() for s in self._skills.values()]
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- PostgreSQL bridge (Phase 10 integration) ----

    def load_from_records(self, records: list[dict[str, Any]]) -> int:
        """Bulk-load skills from repository record dicts (PG rows).

        Each dict must contain at least ``name``; additional keys
        are mapped via :meth:`SkillEntry.from_dict`.  Returns the
        number of skills loaded.
        """
        count = 0
        for rec in records:
            entry = SkillEntry.from_dict(rec)
            self.register(entry)
            count += 1
        return count

    def export_all(self) -> list[dict[str, Any]]:
        """Return all skills as plain dicts suitable for DB persistence."""
        with self._lock:
            return [s.to_dict() for s in self._skills.values()]

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._skills)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "skills": [s.to_dict() for s in self._skills.values()],
                "count": len(self._skills),
            }
