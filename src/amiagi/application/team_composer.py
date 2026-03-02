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


# ---- per-role persona prompts ----

_PERSONA_PROMPTS: dict[str, str] = {
    "backend_developer": (
        "Jesteś doświadczonym programistą backendowym. Implementujesz "
        "API, logikę biznesową, integracje z bazami danych i serwisy. "
        "Piszesz czysty, testowalny kod w Pythonie."
    ),
    "frontend_developer": (
        "Jesteś programistą frontendowym specjalizującym się w React "
        "i TypeScript. Tworzysz responsywne UI, dbasz o UX i dostępność."
    ),
    "tester": (
        "Jesteś testerem QA. Piszesz testy jednostkowe, integracyjne "
        "i E2E. Weryfikujesz poprawność i jakość implementacji."
    ),
    "devops": (
        "Jesteś inżynierem DevOps. Konfigurujesz CI/CD, kontenery Docker, "
        "monitoring, infrastrukturę i automatyzujesz wdrożenia."
    ),
    "researcher": (
        "Jesteś badaczem. Analizujesz technologie, porównujesz rozwiązania "
        "i dostarczasz źródłowe rekomendacje."
    ),
    "data_analyst": (
        "Jesteś analitykiem danych. Analizujesz dane, budujesz pipeline'y "
        "ETL, tworzysz wizualizacje i raporty."
    ),
    "designer": (
        "Jesteś projektantem UX/UI. Projektujesz interfejsy, tworzysz "
        "prototypy i dbasz o spójność designu."
    ),
    "code_reviewer": (
        "Jesteś recenzentem kodu. Analizujesz jakość, bezpieczeństwo, "
        "wydajność i zgodność ze standardami zespołu."
    ),
    "architect": (
        "Jesteś architektem oprogramowania. Projektujesz strukturę systemu, "
        "definiujesz kontrakty między modułami i dbasz o skalowalność."
    ),
}


# ---- per-role model size preference ----

_MODEL_PREFERENCES: dict[str, str] = {
    "architect": "large",
    "code_reviewer": "large",
    "backend_developer": "medium",
    "frontend_developer": "medium",
    "tester": "medium",
    "data_analyst": "medium",
    "researcher": "large",
    "designer": "small",
    "devops": "small",
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

    def __init__(
        self,
        *,
        templates_dir: str | None = None,
        planner_client: Any = None,
    ) -> None:
        self._templates: dict[str, TeamDefinition] = {}
        self._history: list[CompositionAdvice] = []
        self._planner = planner_client
        if templates_dir is not None:
            self._load_templates(templates_dir)

    # ---- templates ----

    def _load_templates(self, templates_dir: str) -> None:
        from pathlib import Path
        d = Path(templates_dir)
        if not d.is_dir():
            return
        for f in d.glob("*"):
            try:
                if f.suffix == ".json":
                    td = TeamDefinition.load_json(f)
                elif f.suffix in (".yaml", ".yml"):
                    td = TeamDefinition.load_yaml(f)
                else:
                    continue
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

    def recommend_with_llm(self, project_description: str) -> CompositionAdvice:
        """LLM-based team recommendation with heuristic fallback.

        Uses the planner client (if available) to generate a richer
        recommendation; falls back to :meth:`recommend` when no LLM
        is configured or when the LLM response cannot be parsed.
        """
        if self._planner is None:
            return self.recommend(project_description)

        import json as _json

        prompt = (
            "Jesteś ekspertem od budowania zespołów programistycznych.\n"
            "Na podstawie opisu projektu zaproponuj optymalny skład zespołu.\n"
            "Zwróć TYLKO JSON (bez markdown fences):\n"
            '{"roles": ["role1", "role2"], "reasoning": "...", "confidence": 0.8}\n\n'
            "Dostępne role: " + ", ".join(sorted(_KEYWORD_MAP.values())) + "\n\n"
            f"Opis projektu:\n{project_description}"
        )
        try:
            raw = self._planner.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="Return ONLY valid JSON, no explanation.",
            )
            # Try to extract JSON
            cleaned = raw.strip()
            if "```" in cleaned:
                for part in cleaned.split("```"):
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("{"):
                        cleaned = part
                        break
            data = _json.loads(cleaned)
            roles = data.get("roles", [])
            if not roles:
                return self.recommend(project_description)
            advice = CompositionAdvice(
                recommended_roles=sorted(set(roles)),
                team_size=len(set(roles)),
                reasoning=data.get("reasoning", "LLM recommendation"),
                confidence=float(data.get("confidence", 0.7)),
                metadata={"source": "llm", "word_count": len(project_description.split())},
            )
            self._history.append(advice)
            return advice
        except Exception:  # noqa: BLE001
            return self.recommend(project_description)

    def smart_recommend(self, project_description: str) -> CompositionAdvice:
        """Best-effort recommendation: tries LLM first, then heuristic."""
        return self.recommend_with_llm(project_description)

    def build_team(self, project_description: str, team_id: str = "") -> TeamDefinition:
        """Create a :class:`TeamDefinition` from a description."""
        advice = self.recommend(project_description)
        if not team_id:
            team_id = "team-" + hashlib.md5(project_description.encode()).hexdigest()[:8]

        members = [
            AgentDescriptor(
                role=role,
                name=role.replace("_", " ").title(),
                persona_prompt=_PERSONA_PROMPTS.get(role, f"Jesteś agentem w roli: {role}."),
                model_preference=_MODEL_PREFERENCES.get(role, "medium"),
            )
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
