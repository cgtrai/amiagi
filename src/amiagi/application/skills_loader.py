"""Skills loader — reads Markdown skill files from a configurable directory.

Skills are loaded per-role (``kastor`` / ``polluks``) and injected into the
system prompt **only** when an API model is active (large-context models).
Local 14B/8B Ollama models have limited context and could be overwhelmed by
extra instructions.

Directory layout expected::

    skills/
      ├── kastor/
      │     ├── code_review.md
      │     └── plan_analysis.md
      └── polluks/
            ├── web_research.md
            └── python_development.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    """A single loaded skill."""

    name: str  # derived from filename without extension
    role: str  # "kastor" | "polluks"
    content: str  # raw Markdown content
    path: Path  # absolute path on disk


@dataclass
class SkillsLoader:
    """Loads Markdown skill files from ``skills_dir/<role>/*.md``."""

    skills_dir: Path = field(default_factory=lambda: Path("./skills"))
    _cache: dict[str, list[Skill]] = field(default_factory=dict, repr=False)

    def load_for_role(self, role: str) -> list[Skill]:
        """Return all skills for *role*, using a cached copy if available."""
        if role in self._cache:
            return self._cache[role]
        skills = self._read_role_dir(role)
        self._cache[role] = skills
        return skills

    def list_available(self) -> dict[str, list[str]]:
        """Return ``{'kastor': ['code_review', …], 'polluks': […]}``."""
        result: dict[str, list[str]] = {}
        role_dir = self.skills_dir
        if not role_dir.is_dir():
            return result
        for child in sorted(role_dir.iterdir()):
            if child.is_dir():
                names = sorted(
                    p.stem for p in child.glob("*.md") if p.is_file() and p.stem
                )
                if names:
                    result[child.name] = names
        return result

    def reload(self) -> None:
        """Clear the cache so the next ``load_for_role`` re-reads from disk."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_role_dir(self, role: str) -> list[Skill]:
        role_path = self.skills_dir / role
        if not role_path.is_dir():
            return []
        skills: list[Skill] = []
        for md_file in sorted(role_path.glob("*.md")):
            if not md_file.is_file():
                continue
            name = md_file.stem
            if not name:
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            skills.append(
                Skill(name=name, role=role, content=content, path=md_file.resolve())
            )
        return skills
