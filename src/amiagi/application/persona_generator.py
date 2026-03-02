"""PersonaGenerator — builds persona prompts from blueprint parameters."""

from __future__ import annotations

from amiagi.application.model_client_protocol import ChatCompletionClient

_PERSONA_TEMPLATE = """\
You are {name}, a highly skilled {team_function}.
Your role in the team: {role}.
Communication style: {style}.

{custom_section}

Always stay focused on your designated function. Be precise, thorough,
and professional.  When unsure, ask clarifying questions rather than
guessing.
"""

_GENERATE_PROMPT = """\
Generate a detailed persona prompt for an AI agent with these traits:
- Name: {name}
- Team function: {team_function}
- Role: {role}
- Communication style: {style}
- Additional context: {context}

Return ONLY the persona prompt text (no JSON, no explanation).
The persona should be 3-6 paragraphs covering: identity, expertise,
communication rules, quality standards, and boundaries.
"""


class PersonaGenerator:
    """Generates persona prompts from templates or via LLM refinement."""

    def __init__(self, client: ChatCompletionClient | None = None) -> None:
        self._client = client

    def generate(
        self,
        *,
        name: str,
        role: str = "executor",
        team_function: str = "",
        communication_style: str = "balanced",
        context: str = "",
        use_llm: bool = False,
    ) -> str:
        """Return a persona prompt.

        When *use_llm* is True and a client is available, the LLM refines
        the prompt.  Otherwise a template-based prompt is returned.
        """
        if use_llm and self._client is not None:
            return self._generate_with_llm(
                name=name,
                role=role,
                team_function=team_function,
                style=communication_style,
                context=context,
            )
        return self._generate_from_template(
            name=name,
            role=role,
            team_function=team_function,
            style=communication_style,
        )

    # ---- internals ----

    @staticmethod
    def _generate_from_template(
        *,
        name: str,
        role: str,
        team_function: str,
        style: str,
    ) -> str:
        return _PERSONA_TEMPLATE.format(
            name=name or "Agent",
            team_function=team_function or role,
            role=role,
            style=style,
            custom_section="",
        ).strip()

    def _generate_with_llm(
        self,
        *,
        name: str,
        role: str,
        team_function: str,
        style: str,
        context: str,
    ) -> str:
        assert self._client is not None
        prompt = _GENERATE_PROMPT.format(
            name=name or "Agent",
            team_function=team_function or role,
            role=role,
            style=style,
            context=context or "(none)",
        )
        try:
            return self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are a prompt engineering expert.",
            ).strip()
        except Exception:
            # Fallback to template on LLM failure
            return self._generate_from_template(
                name=name, role=role, team_function=team_function, style=style,
            )
