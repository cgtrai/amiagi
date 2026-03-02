"""SkillRecommender — suggests matching skills from the catalog."""

from __future__ import annotations

from pathlib import Path

from amiagi.application.model_client_protocol import ChatCompletionClient
from amiagi.application.skills_loader import SkillsLoader

_RECOMMEND_PROMPT = """\
Given the following agent requirements, recommend which existing skills
should be assigned. If no existing skill covers a need, suggest a new
skill name and short description.

Agent function: {team_function}
Required capabilities: {capabilities}

Available skills:
{available}

Return a JSON object:
{{
  "existing": ["skill_name_1", "skill_name_2"],
  "new_suggestions": [
    {{"name": "skill_name", "description": "what it covers"}}
  ]
}}
"""


class SkillRecommender:
    """Recommends skills from the catalog and suggests missing ones."""

    def __init__(
        self,
        skills_loader: SkillsLoader,
        client: ChatCompletionClient | None = None,
    ) -> None:
        self._skills_loader = skills_loader
        self._client = client

    def recommend(
        self,
        *,
        team_function: str,
        capabilities: str = "",
        role: str = "executor",
    ) -> dict[str, list]:
        """Return ``{"existing": [...], "new_suggestions": [...]}``.

        Falls back to keyword matching when no LLM client is available.
        """
        available = self._skills_loader.list_available()
        all_skills = []
        for r, names in available.items():
            for n in names:
                all_skills.append(f"{r}/{n}")

        if self._client is not None:
            return self._recommend_with_llm(
                team_function=team_function,
                capabilities=capabilities,
                available_text="\n".join(all_skills) or "(none)",
            )
        return self._recommend_by_keyword(
            team_function=team_function,
            capabilities=capabilities,
            all_skills=all_skills,
        )

    # ---- internals ----

    @staticmethod
    def _recommend_by_keyword(
        *,
        team_function: str,
        capabilities: str,
        all_skills: list[str],
    ) -> dict[str, list]:
        """Simple keyword-based matching."""
        query_words = {
            w.lower()
            for w in (team_function + " " + capabilities).split()
            if len(w) > 2
        }
        matches = []
        for skill_path in all_skills:
            skill_lower = skill_path.lower()
            for word in query_words:
                if word in skill_lower:
                    matches.append(skill_path)
                    break
        return {"existing": matches, "new_suggestions": []}

    def _recommend_with_llm(
        self,
        *,
        team_function: str,
        capabilities: str,
        available_text: str,
    ) -> dict[str, list]:
        assert self._client is not None
        import json as _json

        prompt = _RECOMMEND_PROMPT.format(
            team_function=team_function,
            capabilities=capabilities or "(general)",
            available=available_text,
        )
        try:
            raw = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a skills advisor. Return ONLY valid JSON.",
            )
            # Try to extract JSON from possibly fenced text
            text = raw.strip()
            if "```" in text:
                parts = text.split("```")
                for part in parts:
                    stripped = part.strip()
                    if stripped.startswith("json"):
                        stripped = stripped[4:].strip()
                    if stripped.startswith("{"):
                        text = stripped
                        break
            data = _json.loads(text)
            return {
                "existing": data.get("existing", []),
                "new_suggestions": data.get("new_suggestions", []),
            }
        except Exception:
            return {"existing": [], "new_suggestions": []}
