from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _slug(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized.strip("-")


def _csv(values: list[str] | tuple[str, ...] | str | None) -> str:
    if values is None:
        return ""
    if isinstance(values, str):
        return values
    return ", ".join(str(item).strip() for item in values if str(item).strip())


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


@dataclass(frozen=True)
class ProjectSkillRecord:
    role: str
    name: str
    display_name: str
    description: str
    content: str
    trigger_keywords: list[str]
    compatible_tools: list[str]
    compatible_roles: list[str]
    priority: int
    path: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "content": self.content,
            "trigger_keywords": list(self.trigger_keywords),
            "compatible_tools": list(self.compatible_tools),
            "compatible_roles": list(self.compatible_roles),
            "priority": self.priority,
            "path": self.path,
            "updated_at": self.updated_at,
            "source": "file",
        }


class ProjectSkillRepository:
    def __init__(self, root_dir: Path) -> None:
        self._root_dir = Path(root_dir)

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def list_skills(self, *, role: str | None = None) -> list[ProjectSkillRecord]:
        if not self._root_dir.exists():
            return []

        records: list[ProjectSkillRecord] = []
        roles = [role] if role else [child.name for child in sorted(self._root_dir.iterdir()) if child.is_dir()]
        for item_role in roles:
            role_slug = _slug(item_role)
            if not role_slug:
                continue
            role_dir = self._root_dir / role_slug
            if not role_dir.is_dir():
                continue
            for path in sorted(role_dir.glob("*.md")):
                record = self._read_record(path, role_slug)
                if record is not None:
                    records.append(record)
        return records

    def get_skill(self, role: str, name: str) -> ProjectSkillRecord | None:
        path = self._skill_path(role, name)
        if not path.is_file():
            return None
        return self._read_record(path, _slug(role))

    def upsert_skill(self, *, role: str, name: str, content: str, display_name: str = "", description: str = "", trigger_keywords: list[str] | None = None, compatible_tools: list[str] | None = None, compatible_roles: list[str] | None = None, priority: int = 50) -> ProjectSkillRecord:
        role_slug = _slug(role)
        name_slug = _slug(name)
        if not role_slug or not name_slug:
            raise ValueError("role_and_name_required")

        role_dir = self._root_dir / role_slug
        role_dir.mkdir(parents=True, exist_ok=True)
        path = role_dir / f"{name_slug}.md"

        metadata_lines = [
            "---",
            f"name: {name_slug}",
            f"display_name: {display_name or name_slug}",
            f"description: {description or ''}",
            f"trigger_keywords: {_csv(trigger_keywords or [])}",
            f"compatible_tools: {_csv(compatible_tools or [])}",
            f"compatible_roles: {_csv(compatible_roles or [role_slug])}",
            f"priority: {int(priority)}",
            "---",
            str(content or "").rstrip() + "\n",
        ]
        path.write_text("\n".join(metadata_lines), encoding="utf-8")

        record = self._read_record(path, role_slug)
        if record is None:
            raise RuntimeError("project_skill_write_failed")
        return record

    def delete_skill(self, role: str, name: str) -> bool:
        path = self._skill_path(role, name)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def _skill_path(self, role: str, name: str) -> Path:
        return self._root_dir / _slug(role) / f"{_slug(name)}.md"

    def _read_record(self, path: Path, role: str) -> ProjectSkillRecord | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None

        metadata: dict[str, str] = {}
        body = raw
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[0].strip() == "---":
            try:
                end_index = lines[1:].index("---") + 1
            except ValueError:
                end_index = -1
            if end_index > 0:
                for line in lines[1:end_index]:
                    key, sep, value = line.partition(":")
                    if sep:
                        metadata[key.strip()] = value.strip()
                body = "\n".join(lines[end_index + 1 :]).strip()

        stat = path.stat()
        return ProjectSkillRecord(
            role=role,
            name=metadata.get("name", path.stem),
            display_name=metadata.get("display_name", path.stem),
            description=metadata.get("description", ""),
            content=body,
            trigger_keywords=_parse_csv(metadata.get("trigger_keywords", "")),
            compatible_tools=_parse_csv(metadata.get("compatible_tools", "")),
            compatible_roles=_parse_csv(metadata.get("compatible_roles", role)),
            priority=int(metadata.get("priority", "50") or "50"),
            path=str(path),
            updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        )