from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_WORD_RE = re.compile(r"\b\w{3,}\b", re.IGNORECASE)
_DEFAULT_AGENT_ROLES = {"polluks", "kastor"}


def _estimate_token_cost(text: str, explicit_cost: int | None) -> int:
    if explicit_cost is not None and explicit_cost > 0:
        return int(explicit_cost)
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class _RuntimeSkill:
    skill_id: str
    name: str
    display_name: str
    content: str
    trigger_keywords: tuple[str, ...]
    compatible_tools: tuple[str, ...]
    compatible_roles: tuple[str, ...]
    token_cost: int
    priority: int
    source: str


@dataclass(frozen=True)
class _RuntimeTrait:
    trait_id: str
    agent_role: str
    trait_type: str
    name: str
    content: str
    token_cost: int
    priority: int


@dataclass(frozen=True)
class _RuntimeSnapshot:
    skills: tuple[_RuntimeSkill, ...]
    traits: tuple[_RuntimeTrait, ...]
    pinned_by_role: dict[str, frozenset[str]]
    refreshed_at: str


class RuntimeSkillProvider:
    def __init__(
        self,
        *,
        token_budget: int = 1800,
        trait_budget: int = 700,
    ) -> None:
        self._token_budget = max(200, int(token_budget))
        self._trait_budget = max(100, int(trait_budget))
        self._lock = threading.RLock()
        self._snapshot = _RuntimeSnapshot(
            skills=(),
            traits=(),
            pinned_by_role={},
            refreshed_at="",
        )

    async def refresh(self, repository: Any, project_repository: Any | None = None) -> None:
        skills = await repository.list_skills(active_only=True)
        traits = await repository.list_traits(active_only=True)
        project_skills = project_repository.list_skills() if project_repository is not None else []

        roles = set(_DEFAULT_AGENT_ROLES)
        for trait in traits:
            role = str(getattr(trait, "agent_role", "") or "").strip().lower()
            if role:
                roles.add(role)
        for skill in list(skills) + list(project_skills):
            for role in list(getattr(skill, "compatible_roles", []) or []):
                normalized = str(role or "").strip().lower()
                if normalized:
                    roles.add(normalized)

        pinned_by_role: dict[str, frozenset[str]] = {}
        for role in sorted(roles):
            pinned_ids = await repository.get_pinned_skills(role)
            pinned_by_role[role] = frozenset(str(item) for item in pinned_ids)

        db_skill_snapshot = tuple(
            _RuntimeSkill(
                skill_id=str(skill.id),
                name=str(skill.name),
                display_name=str(skill.display_name or skill.name),
                content=str(skill.content),
                trigger_keywords=tuple(str(item).strip().lower() for item in (skill.trigger_keywords or []) if str(item).strip()),
                compatible_tools=tuple(str(item).strip() for item in (skill.compatible_tools or []) if str(item).strip()),
                compatible_roles=tuple(str(item).strip().lower() for item in (skill.compatible_roles or []) if str(item).strip()),
                token_cost=_estimate_token_cost(str(skill.content), getattr(skill, "token_cost", 0)),
                priority=int(getattr(skill, "priority", 0) or 0),
                source="db",
            )
            for skill in skills
        )
        project_skill_snapshot = tuple(
            _RuntimeSkill(
                skill_id=f"file:{skill.role}:{skill.name}",
                name=str(skill.name),
                display_name=str(skill.display_name or skill.name),
                content=str(skill.content),
                trigger_keywords=tuple(str(item).strip().lower() for item in (skill.trigger_keywords or []) if str(item).strip()),
                compatible_tools=tuple(str(item).strip() for item in (skill.compatible_tools or []) if str(item).strip()),
                compatible_roles=tuple(str(item).strip().lower() for item in (skill.compatible_roles or []) if str(item).strip()),
                token_cost=_estimate_token_cost(str(skill.content), 0),
                priority=int(getattr(skill, "priority", 0) or 0),
                source="file",
            )
            for skill in project_skills
        )
        trait_snapshot = tuple(
            _RuntimeTrait(
                trait_id=str(trait.id),
                agent_role=str(trait.agent_role or "").strip().lower(),
                trait_type=str(trait.trait_type or "").strip().lower(),
                name=str(trait.name),
                content=str(trait.content),
                token_cost=_estimate_token_cost(str(trait.content), getattr(trait, "token_cost", 0)),
                priority=int(getattr(trait, "priority", 0) or 0),
            )
            for trait in traits
        )

        snapshot = _RuntimeSnapshot(
            skills=db_skill_snapshot + project_skill_snapshot,
            traits=trait_snapshot,
            pinned_by_role=pinned_by_role,
            refreshed_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._snapshot = snapshot

    def _ranked_skills_for_role(
        self,
        agent_role: str,
        prompt: str,
        available_tools: list[str] | None = None,
    ) -> tuple[_RuntimeSnapshot, list[tuple[int, _RuntimeSkill, str]]]:
        normalized_role = str(agent_role or "").strip().lower()
        prompt_words = set(word.lower() for word in _WORD_RE.findall(prompt or ""))
        tools_set = {str(item).strip() for item in (available_tools or []) if str(item).strip()}

        with self._lock:
            snapshot = self._snapshot

        pinned_ids = snapshot.pinned_by_role.get(normalized_role, frozenset())
        candidates: list[tuple[int, _RuntimeSkill, str]] = []
        for skill in snapshot.skills:
            if skill.compatible_roles and normalized_role not in skill.compatible_roles:
                continue
            keyword_score = len(prompt_words.intersection(skill.trigger_keywords))
            tool_score = len(tools_set.intersection(skill.compatible_tools))
            is_pinned = skill.skill_id in pinned_ids
            source_bias = 10 if skill.source == "db" else 0
            total_score = skill.priority + keyword_score * 2 + tool_score + source_bias + (1000 if is_pinned else 0)
            if total_score <= 0:
                continue
            if is_pinned:
                reason = "pinned"
            elif keyword_score > 0:
                reason = "keyword"
            elif tool_score > 0:
                reason = "tool"
            else:
                reason = "role"
            candidates.append((total_score, skill, reason))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return snapshot, candidates

    def select(
        self,
        agent_role: str,
        prompt: str,
        available_tools: list[str] | None = None,
    ) -> list[dict[str, str]]:
        normalized_role = str(agent_role or "").strip().lower()
        snapshot, candidates = self._ranked_skills_for_role(normalized_role, prompt, available_tools)

        used_tokens = 0
        selected: list[dict[str, str]] = []

        role_traits = sorted(
            [trait for trait in snapshot.traits if trait.agent_role == normalized_role],
            key=lambda trait: trait.priority,
            reverse=True,
        )
        trait_tokens = 0
        for trait in role_traits:
            if trait_tokens + trait.token_cost > self._trait_budget and selected:
                continue
            selected.append(
                {
                    "name": f"trait:{trait.name}",
                    "content": trait.content,
                }
            )
            trait_tokens += trait.token_cost
            used_tokens += trait.token_cost

        for _, skill, reason in candidates:
            if used_tokens + skill.token_cost > self._token_budget:
                continue
            selected.append(
                {
                    "name": f"skill:{skill.name}",
                    "content": skill.content,
                    "match_reason": reason,
                    "source": skill.source,
                }
            )
            used_tokens += skill.token_cost

        return selected

    def recommend(
        self,
        agent_role: str,
        prompt: str,
        available_tools: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        _snapshot, candidates = self._ranked_skills_for_role(agent_role, prompt, available_tools)
        recommendations: list[dict[str, Any]] = []
        for _, skill, reason in candidates[:8]:
            recommendations.append(
                {
                    "name": skill.name,
                    "display_name": skill.display_name,
                    "compatible_tools": list(skill.compatible_tools),
                    "compatible_roles": list(skill.compatible_roles),
                    "priority": skill.priority,
                    "match_reason": reason,
                    "source": skill.source,
                }
            )
        return recommendations

    @property
    def refreshed_at(self) -> str:
        with self._lock:
            return self._snapshot.refreshed_at