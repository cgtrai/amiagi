"""SkillSelector — on-demand skill selection for agent prompts.

Multi-level matching pipeline:
1. Rule-based: trigger_keywords matched against prompt tokens
2. Tool-matching: skills compatible with agent's available tools
3. Role filter: compatible_roles contains agent role
4. Pinned skills: always included (agent_skill_assignments.is_pinned)
5. Token budget: sort by priority DESC, trim to fit budget
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"\b\w{3,}\b", re.IGNORECASE)


@dataclass
class SelectedSkill:
    """A skill chosen by the selector for injection into the system prompt."""

    skill_id: str
    name: str
    display_name: str
    content: str
    token_cost: int
    priority: int
    match_reason: str  # 'keyword', 'tool', 'role', 'pinned'

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "display_name": self.display_name,
            "token_cost": self.token_cost,
            "priority": self.priority,
            "match_reason": self.match_reason,
        }


class SkillSelector:
    """Selects the most relevant skills within a token budget."""

    def __init__(self, pool: "asyncpg.Pool", token_budget: int = 2000) -> None:
        self._pool = pool
        self._token_budget = token_budget

    @property
    def token_budget(self) -> int:
        return self._token_budget

    async def select(
        self,
        prompt: str,
        agent_role: str,
        available_tools: list[str] | None = None,
    ) -> list[SelectedSkill]:
        """Return ranked skills fitting the token budget.

        Pipeline:
        1. Fetch all active skills compatible with *agent_role*
        2. Score by keyword overlap and tool compatibility
        3. Include pinned skills unconditionally
        4. Sort by score+priority, trim to budget
        """
        prompt_words = set(w.lower() for w in _WORD_RE.findall(prompt))
        tools_set = set(available_tools or [])

        # Fetch candidate skills
        rows = await self._pool.fetch(
            """
            SELECT s.*, COALESCE(a.is_pinned, false) AS is_pinned
            FROM dbo.skills s
            LEFT JOIN dbo.agent_skill_assignments a
                ON a.skill_id = s.id AND a.agent_role = $1
            WHERE s.is_active = true
              AND ($1 = ANY(s.compatible_roles) OR cardinality(s.compatible_roles) = 0)
            ORDER BY s.priority DESC
            """,
            agent_role,
        )

        candidates: list[tuple[int, SelectedSkill]] = []
        pinned: list[SelectedSkill] = []

        for row in rows:
            # Safety: skip inactive skills (normally filtered by SQL)
            if not row.get("is_active", True):
                continue
            skill_keywords = set(k.lower() for k in (row["trigger_keywords"] or []))
            skill_tools = set(row["compatible_tools"] or [])

            # Score: keyword overlap + tool overlap
            keyword_score = len(prompt_words & skill_keywords)
            tool_score = len(tools_set & skill_tools) if tools_set else 0
            total_score = keyword_score * 2 + tool_score + row["priority"]

            reason = "keyword" if keyword_score > 0 else ("tool" if tool_score > 0 else "role")

            selected = SelectedSkill(
                skill_id=str(row["id"]),
                name=row["name"],
                display_name=row["display_name"],
                content=row["content"],
                token_cost=row["token_cost"],
                priority=row["priority"],
                match_reason=reason,
            )

            if row["is_pinned"]:
                selected.match_reason = "pinned"
                pinned.append(selected)
            elif total_score > 0:
                candidates.append((total_score, selected))

        # Sort candidates by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)

        # Build result within token budget
        result: list[SelectedSkill] = []
        used_tokens = 0

        # Pinned first
        for s in pinned:
            used_tokens += s.token_cost
            result.append(s)

        # Then ranked candidates
        for _, s in candidates:
            if used_tokens + s.token_cost > self._token_budget:
                continue
            used_tokens += s.token_cost
            result.append(s)

        return result

    async def log_usage(
        self,
        skill_id: str,
        agent_role: str,
        task_summary: str = "",
        was_useful: bool | None = None,
        tokens_used: int = 0,
    ) -> None:
        """Record skill usage to the log table."""
        await self._pool.execute(
            """
            INSERT INTO dbo.skill_usage_log (skill_id, agent_role, task_summary, was_useful, tokens_used)
            VALUES ($1::uuid, $2, $3, $4, $5)
            """,
            skill_id, agent_role, task_summary, was_useful, tokens_used,
        )
