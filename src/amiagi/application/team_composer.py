"""Phase 11 — Team composer (application).

Recommends team compositions based on project descriptions
using heuristics (and optionally LLM).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from amiagi.domain.team_definition import AgentDescriptor, TeamDefinition


# ---- heuristic helpers ----

_KEYWORD_MAP: dict[str, str] = {
    "backend": "backend_developer",
    "api": "backend_developer",
    "frontend": "frontend_developer",
    "ui": "frontend_developer",
    "react": "frontend_developer",
    "test": "tester",
    "qa": "tester",
    "deploy": "devops",
    "docker": "devops",
    "ci/cd": "devops",
    "research": "researcher",
    "search": "researcher",
    "data": "data_analyst",
    "ml": "data_analyst",
    "design": "designer",
    "ux": "designer",
    "review": "code_reviewer",
    "architect": "architect",
    "plan": "architect",
}


@dataclass
class CompositionAdvice:
    """Recommendation produced by TeamComposer."""

    recommended_roles: list[str] = field(default_factory=list)
    team_size: int = 0
    reasoning: str = ""
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_roles": self.recommended_roles,
            "team_size": self.team_size,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


class TeamComposer:
    """Recommends team compositions from a project description."""

    def __init__(self, *, templates_dir: str | None = None) -> None:
        self._templates: dict[str, TeamDefinition] = {}
        self._history: list[CompositionAdvice] = []
        if templates_dir is not None:
            self._load_templates(templates_dir)

    # ---- templates ----

    def _load_templates(self, templates_dir: str) -> None:
        from pathlib import Path
        d = Path(templates_dir)
        if not d.is_dir():
            return
        for f in d.glob("*.json"):
            try:
                td = TeamDefinition.load_json(f)
                self._templates[td.team_id or f.stem] = td
            except Exception:  # noqa: BLE001
                pass

    def register_template(self, template: TeamDefinition) -> None:
        self._templates[template.team_id] = template

    def list_templates(self) -> list[str]:
        return list(self._templates.keys())

    def get_template(self, template_id: str) -> TeamDefinition | None:
        return self._templates.get(template_id)

    # ---- composition ----

    def recommend(self, project_description: str) -> CompositionAdvice:
        """Heuristic-based team recommendation."""
        lower = project_description.lower()
        matched_roles: dict[str, int] = {}
        for keyword, role in _KEYWORD_MAP.items():
            if keyword in lower:
                matched_roles[role] = matched_roles.get(role, 0) + 1

        if not matched_roles:
            # Default fallback: small generic team
            matched_roles = {"backend_developer": 1, "tester": 1}

        # Always include architect for complex descriptions
        word_count = len(project_description.split())
        if word_count > 50 and "architect" not in matched_roles:
            matched_roles["architect"] = 1

        roles = sorted(matched_roles.keys())
        team_size = len(roles)
        confidence = min(1.0, len(matched_roles) / 5.0)

        reasoning_parts = [f"Detected roles from keywords: {', '.join(roles)}."]
        if word_count > 50:
            reasoning_parts.append("Complex description → added architect.")

        advice = CompositionAdvice(
            recommended_roles=roles,
            team_size=team_size,
            reasoning=" ".join(reasoning_parts),
            confidence=round(confidence, 2),
            metadata={"word_count": word_count, "matched_keywords": len(matched_roles)},
        )
        self._history.append(advice)
        return advice

    def build_team(self, project_description: str, team_id: str = "") -> TeamDefinition:
        """Create a :class:`TeamDefinition` from a description."""
        advice = self.recommend(project_description)
        if not team_id:
            team_id = "team-" + hashlib.md5(project_description.encode()).hexdigest()[:8]

        members = [
            AgentDescriptor(role=role, name=role.replace("_", " ").title())
            for role in advice.recommended_roles
        ]
        lead = members[0].role if members else ""

        return TeamDefinition(
            team_id=team_id,
            name=f"Team {team_id}",
            members=members,
            lead_agent_id=lead,
            project_context=project_description,
            metadata={"advice": advice.to_dict()},
        )

    # ---- from template ----

    def from_template(self, template_id: str, project_context: str = "") -> TeamDefinition | None:
        tmpl = self._templates.get(template_id)
        if tmpl is None:
            return None
        import copy
        team = copy.deepcopy(tmpl)
        if project_context:
            team.project_context = project_context
        return team

    # ---- history ----

    def history(self) -> list[CompositionAdvice]:
        return list(self._history)
